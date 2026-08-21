#!/usr/bin/env python3
"""Small deterministic check used by the bundled example."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


source = Path(sys.argv[1])
output = Path(sys.argv[2])
text = source.read_text(encoding="utf-8")
result = {
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "claims_95_percent": text.count("95%"),
    "mentions_image_level_split": "image level" in text.lower(),
    "mentions_confidence_interval": "confidence interval" in text.lower(),
}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
