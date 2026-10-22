"""Export language selection and the model-adapter translation hook.

`en` and `zh-Hans` are rendered from `LocalizedText`. `execute_review` with
`bilingual = true` is the existing model path that fills `zh-Hans`. Any other
BCP-47 tag needs a report-stage `translator` callable. This module resolves
the tag and loads that callable. It does not call a model by itself.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from typing import Any

from .i18n import MissingTranslationError
from .models import ReviewPackage

_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")

HOOK_MESSAGE = (
    "No {language} translation is available. "
    "Simplified Chinese is produced when execute_review sets bilingual = true, "
    "which runs the zh-Hans pass in autopaperreview.model_exec. "
    "Other BCP-47 tags, and zh-Hans text the phrase table cannot fill, need a "
    "report translator. Set translator = \"module:attribute\" to a function "
    "translate_package(package, language, **options) -> ReviewPackage, or to a "
    "class with that method. translator_base_url, translator_model, "
    "translator_api_key_env, and translator_allow_remote are forwarded when set. "
    "autopaperreview.model_exec:translate_package is that model-adapter hook."
)


def canonical_bcp47(tag: str) -> str:
    parts = tag.split("-")
    canonical = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            canonical.append(part[:1].upper() + part[1:].lower())
        elif len(part) == 2 and part.isalpha():
            canonical.append(part.upper())
        else:
            canonical.append(part)
    return "-".join(canonical)


def parse_export_lang(value: str) -> list[str]:
    """Parse `--lang en`, `zh-Hans`, `bilingual`, or another BCP-47 tag."""
    token = value.strip()
    if not token:
        raise ValueError("report language cannot be blank")
    if token.lower() == "bilingual":
        return ["en", "zh-Hans"]
    if not _TAG.match(token):
        raise ValueError(
            f"unsupported language {value!r}; expected en, zh-Hans, bilingual, or a BCP-47 tag"
        )
    return [canonical_bcp47(token)]


def hook_error(language: str) -> MissingTranslationError:
    return MissingTranslationError(HOOK_MESSAGE.format(language=language))


Translator = Callable[..., ReviewPackage]


def load_translator(spec: str) -> Any:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not attribute:
        raise ValueError("translator must be a module:attribute reference")
    module = importlib.import_module(module_name)
    return getattr(module, attribute)


def apply_translator(
    spec: str,
    package: ReviewPackage,
    language: str,
    options: dict[str, Any],
) -> ReviewPackage:
    """Call the configured translator. Extra options are forwarded as keywords."""
    loaded = load_translator(spec)
    target = loaded() if isinstance(loaded, type) else loaded
    method = getattr(target, "translate_package", None)
    caller = method if method is not None else target
    cleaned = {key: value for key, value in options.items() if value is not None}
    return caller(package, language, **cleaned)
