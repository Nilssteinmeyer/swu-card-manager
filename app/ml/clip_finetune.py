"""
Fine-tune CLIP visual encoder on synthetic card image pairs.

Training approach (following professional TCG scanner methodology):
  - Anchor: clean reference card image
  - Positive: augmented version (perspective, noise, blur, color jitter)
  - Loss: InfoNCE contrastive loss — pulls anchor and positive together,
    pushes apart from other cards in the batch

This closes the "domain gap" between clean digital card images and
real phone photos, teaching CLIP to recognize cards despite:
  - Perspective distortion
  - Lighting changes
  - Background clutter
  - Camera noise and blur
  - JPEG compression

After fine-tuning, the CLIP model produces embeddings where a real photo
of a card is close to the clean reference image of the same card.
"""
from __future__ import annotations

import time
import random
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset
from PIL import Image

from app.core.config import AppConfig
from app.core.logging import get_logger

log = get_logger("clip_training")


class CardDataset(Dataset):
    """Dataset that loads card images and applies on-the-fly augmentation."""

    def __init__(self, card_images: list[tuple[str, str]], preprocess, augment: bool = True):
        self.card_images = card_images
        self.preprocess = preprocess
        self.augment = augment

    def __len__(self):
        return len(self.card_images)

    def _augment_numpy(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        margin = random.randint(10, 40)
        src = np.float32([[0,0],[w-1,0],[w-1,h-1],[0,h-1]])
        dst = np.float32([
            [random.randint(0, margin), random.randint(0, margin)],
            [w-1-random.randint(0, margin), random.randint(0, margin)],
            [w-1-random.randint(0, margin), h-1-random.randint(0, margin)],
            [random.randint(0, margin), h-1-random.randint(0, margin)],
        ])
        M = cv2.getPerspectiveTransform(src, dst)
        img = cv2.warpPerspective(img, M, (w, h))
        angle = random.uniform(-5, 5)
        M2 = cv2.getRotationMatrix2D((w/2, h/2), angle, 1.0)
        img = cv2.warpAffine(img, M2, (w, h))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        hsv[:,:,0] = np.clip(hsv[:,:,0].astype(int) + random.randint(-15, 15), 0, 255).astype(np.uint8)
        hsv[:,:,1] = np.clip(hsv[:,:,1].astype(int) + random.randint(-30, 30), 0, 255).astype(np.uint8)
        hsv[:,:,2] = np.clip(hsv[:,:,2].astype(int) + random.randint(-30, 30), 0, 255).astype(np.uint8)
        img = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        noise = np.random.normal(0, random.uniform(5, 15), img.shape).astype(np.uint8)
        img = cv2.add(img, noise)
        img = cv2.GaussianBlur(img, (random.choice([3,5]),)*2, 0)
        _, encoded = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), random.randint(50, 80)])
        img = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        return img

    def __getitem__(self, idx):
        card_id, img_path = self.card_images[idx]
        img = cv2.imread(img_path)
        if img is None:
            card_id, img_path = self.card_images[random.randint(0, len(self.card_images)-1)]
            img = cv2.imread(img_path)
            if img is None:
                img = np.zeros((1050, 750, 3), dtype=np.uint8)
        h, w = img.shape[:2]
        if w > h:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        img = cv2.resize(img, (224, 224))
        orig_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        orig_pil = Image.fromarray(orig_rgb)
        orig_tensor = self.preprocess(orig_pil)
        if self.augment:
            aug_img = self._augment_numpy(img)
            aug_rgb = cv2.cvtColor(aug_img, cv2.COLOR_BGR2RGB)
            aug_pil = Image.fromarray(aug_rgb)
            aug_tensor = self.preprocess(aug_pil)
        else:
            aug_tensor = orig_tensor
        return orig_tensor, aug_tensor, idx


def fine_tune_clip(
    epochs: int = 5,
    batch_size: int = 128,
    lr: float = 1e-6,
    temperature: float = 0.07,
):
    """Fine-tune CLIP visual encoder with on-the-fly GPU augmentation."""
    import open_clip

    cfg = AppConfig.load()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info(f"Fine-tuning CLIP on {device}", extra={"event": "clip_train_start"})

    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
    model = model.to(device)
    for name, param in model.named_parameters():
        param.requires_grad = "visual" in name
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log.info(f"Trainable params: {trainable:,}")

    from app.db.schema import connect
    from app.db import repository as repo
    conn = connect()
    cards = repo.get_all_cards(conn)
    conn.close()
    card_images = [(c["card_id"], c["front_art_path"]) for c in cards
                   if c.get("front_art_path") and Path(c["front_art_path"]).exists()]
    log.info(f"Training on {len(card_images)} card images", extra={"event": "clip_train_data"})

    dataset = CardDataset(card_images, preprocess, augment=True)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True,
                           num_workers=0, pin_memory=True, drop_last=True)

    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
                                  lr=lr, weight_decay=0.01)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs * len(dataloader))
    model.train()
    scaler = torch.amp.GradScaler('cuda') if device == "cuda" else None

    for epoch in range(epochs):
        epoch_loss = 0.0
        num_batches = 0
        t0 = time.time()
        for batch_idx, (orig_imgs, aug_imgs, _) in enumerate(dataloader):
            orig_imgs = orig_imgs.to(device, non_blocking=True)
            aug_imgs = aug_imgs.to(device, non_blocking=True)
            optimizer.zero_grad()
            if device == "cuda":
                with torch.amp.autocast('cuda'):
                    orig_features = model.encode_image(orig_imgs)
                    aug_features = model.encode_image(aug_imgs)
                    orig_features = F.normalize(orig_features.float(), dim=-1)
                    aug_features = F.normalize(aug_features.float(), dim=-1)
                    logits = torch.matmul(orig_features, aug_features.T) / temperature
                    labels = torch.arange(batch_size, device=device)
                    loss = (F.cross_entropy(logits, labels) + F.cross_entropy(logits.T, labels)) / 2
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            else:
                orig_features = model.encode_image(orig_imgs)
                aug_features = model.encode_image(aug_imgs)
                orig_features = F.normalize(orig_features.float(), dim=-1)
                aug_features = F.normalize(aug_features.float(), dim=-1)
                logits = torch.matmul(orig_features, aug_features.T) / temperature
                labels = torch.arange(batch_size, device=device)
                loss = (F.cross_entropy(logits, labels) + F.cross_entropy(logits.T, labels)) / 2
                loss.backward()
                optimizer.step()
            scheduler.step()
            epoch_loss += loss.item()
            num_batches += 1
            if batch_idx % 20 == 0:
                log.info(f"Epoch {epoch+1}/{epochs} Batch {batch_idx}/{len(dataloader)} "
                         f"loss={loss.item():.4f} lr={scheduler.get_last_lr()[0]:.2e}",
                         extra={"event": "clip_train_batch"})
        elapsed = time.time() - t0
        avg_loss = epoch_loss / max(num_batches, 1)
        log.info(f"Epoch {epoch+1}/{epochs} done: avg_loss={avg_loss:.4f} in {elapsed:.1f}s",
                 extra={"event": "clip_train_epoch"})

    models_dir = cfg.path("models_dir")
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / "clip_finetuned.pt"
    torch.save(model.state_dict(), str(model_path))
    log.info(f"Fine-tuned model saved to {model_path}", extra={"event": "clip_train_done"})
    return str(model_path)


if __name__ == "__main__":
    AppConfig.load()
    from app.core.logging import AppLogger
    AppLogger.setup()
    fine_tune_clip()
