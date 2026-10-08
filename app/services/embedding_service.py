"""DINOv2 embedding generation for product images."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import torch
import torch.nn.functional as functional
from PIL import Image
from torchvision import transforms

if TYPE_CHECKING:
    from torchvision.transforms import Compose

logger = logging.getLogger(__name__)

_DINOV2_VECTOR_SIZES: dict[str, int] = {
    "dinov2_vits14": 384,
    "dinov2_vitb14": 768,
    "dinov2_vitl14": 1024,
    "dinov2_vitg14": 1536,
}


class EmbeddingService:
    """Load DINOv2 once and produce normalized image embeddings."""

    def __init__(self, model_name: str) -> None:
        """Store the hub model name to load on startup.

        Args:
            model_name: Torch hub DINOv2 variant, for example ``dinov2_vits14``.
        """
        self._model_name = model_name
        self._model: torch.nn.Module | None = None
        self._transform: Compose | None = None

    @property
    def vector_size(self) -> int:
        """Return the embedding dimension for the configured model.

        Returns:
            Vector length produced by the model.

        Raises:
            ValueError: If the model name is not mapped to a vector size.
        """
        size = _DINOV2_VECTOR_SIZES.get(self._model_name)
        if size is None:
            raise ValueError(f"Unsupported DINOv2 model: {self._model_name}")
        return size

    def load(self) -> None:
        """Load DINOv2 and image preprocessing into memory.

        Raises:
            RuntimeError: If the hub model cannot be loaded.
        """
        if self._model is not None:
            return
        try:
            model = torch.hub.load("facebookresearch/dinov2", self._model_name)
            model.eval()
            self._model = model
            self._transform = self._build_transform()
            logger.info("Loaded DINOv2 model %s", self._model_name)
        except Exception as exc:
            logger.exception("Failed to load DINOv2 model %s", self._model_name)
            raise RuntimeError("Unable to load DINOv2 model.") from exc

    def embed(self, image: Image.Image) -> list[float]:
        """Generate a unit-length embedding for an RGB image.

        Args:
            image: PIL image in RGB mode.

        Returns:
            Normalized embedding as a Python list of floats.

        Raises:
            RuntimeError: If the model has not been loaded yet.
        """
        if self._model is None or self._transform is None:
            raise RuntimeError("Embedding model is not loaded.")
        rgb_image = image.convert("RGB")
        tensor = self._transform(rgb_image).unsqueeze(0)
        with torch.no_grad():
            features = self._model(tensor)
            if features.ndim > 1:
                features = features.squeeze(0)
            normalized = functional.normalize(features, p=2, dim=0)
        return normalized.cpu().tolist()

    def _build_transform(self) -> Compose:
        """Build the torchvision preprocessing pipeline for DINOv2.

        Returns:
            Compose transform for a single image tensor.
        """
        return transforms.Compose(
            [
                transforms.Resize(256),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=(0.485, 0.456, 0.406),
                    std=(0.229, 0.224, 0.225),
                ),
            ]
        )
