"""Tests for CUCM AI Assistant Copy button, viewport legacy fallback, manual-copy modal, and diagnostics."""

from pathlib import Path


def test_copy_button_implementation_and_fallback_present():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    css = Path("frontend/assets/css/dashboard.css").read_text(encoding="utf-8")
    js = Path("frontend/assets/js/dashboard.js").read_text(encoding="utf-8")

    # Manual copy modal & toast & cache-busting
    assert 'id="manualCopyModal"' in html
    assert 'id="manualCopyTextarea"' in html
    assert "Clipboard access unavailable – Copy manually" in html
    assert 'id="copyToast"' in html
    assert "dashboard.js?v=5.2" in html

    # CSS user-select & modal styles
    assert "user-select: text" in css
    assert ".copy-btn" in css
    assert ".copy-btn.copy-success" in css
    assert ".copy-btn.copy-error" in css
    assert ".manual-copy-modal" in css

    # Synchronous copy handler, viewport fallback, diagnostics, and manual modal
    assert "function copyAssistantResponse(" in js
    assert "function legacyCopyInViewport(" in js
    assert "function openManualCopyModal(" in js
    assert "function initChatCopyDelegation(" in js
    assert "top = '12px'" in js and "left = '12px'" in js
    assert "window.isSecureContext" in js
    assert "document.execCommand('copy')" in js
    assert "document.body.removeChild(ta)" in js
    assert "Copied ✓" in js
    assert "Copy manually" in js
    assert "window.__lastCopyDiag" in js
    assert "this.closest('.chat-bubble').innerText" not in js
