"""Source contracts for Agent-authored UI rendered inside an opaque iframe.

These checks catch broken resource use and obvious document mistakes. They are
not a JavaScript security sandbox. The browser's sandbox, CSP and host bridge
are the execution boundary; generated code is never evaluated on the server.
"""
from __future__ import annotations

from html.parser import HTMLParser
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GeneratedSurface(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(default="自定义行程界面", min_length=1, max_length=100)
    html: str = Field(min_length=1, max_length=30000)
    css: str = Field(default="", max_length=16000)
    javascript: str = Field(default="", max_length=20000)
    placement: Literal["beforeStops", "afterStops", "replaceStops"] = "afterStops"

    @model_validator(mode="after")
    def source_contract(self):
        validate_surface(self)
        return self


class _MarkupContract(HTMLParser):
    blocked_tags = {"script", "iframe", "frame", "frameset", "object", "embed", "base", "meta", "link", "style"}
    resource_attributes = {"src", "srcset", "poster", "action", "formaction", "data", "background", "ping"}

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() in self.blocked_tags:
            raise ValueError("surface HTML must be body markup; put CSS and JavaScript in their separate fields")
        for name, value in attrs:
            name = name.lower()
            value = (value or "").strip()
            if name.startswith("on"):
                raise ValueError("surface event handlers belong in the javascript field")
            if name in self.resource_attributes and value:
                raise ValueError("surface cannot load external resources; use inline SVG and the travel-map component")
            if name in {"href", "xlink:href"} and value and not value.startswith("#"):
                raise ValueError("surface links must target local anchors; use the host's map component for real map links")
            if name in {"srcdoc", "http-equiv"}:
                raise ValueError("nested documents are not supported inside a generated surface")

    handle_startendtag = handle_starttag


def validate_surface(surface: GeneratedSurface | dict) -> None:
    """Validate a complete source object, without executing any authored code."""
    if isinstance(surface, dict):
        surface = GeneratedSurface.model_validate(surface)
    if not surface.html.strip():
        raise ValueError("generated surface must contain visible body markup")
    if "\x00" in surface.html + surface.css + surface.javascript:
        raise ValueError("generated source contains a NUL character")
    parser = _MarkupContract(convert_charrefs=True)
    parser.feed(surface.html)
    parser.close()
    if re.search(r"@\s*import\b", surface.css, re.I):
        raise ValueError("surface styles cannot import external resources")
    for match in re.finditer(r"url\s*\(\s*(['\"]?)(.*?)\1\s*\)", surface.css, re.I | re.S):
        if not match.group(2).strip().startswith("#"):
            raise ValueError("surface styles cannot load external resources")
    if re.search(r"</\s*(?:script|style)\b", surface.javascript + surface.css, re.I):
        raise ValueError("surface source must not close the host's script or style containers")
