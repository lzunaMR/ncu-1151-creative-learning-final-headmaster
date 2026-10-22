"""啟動時檢查主機上的 Ollama：連得到嗎？需要的模型都在嗎？缺的就自動下載。

目的：demo 時不必手動 ollama pull，連不到 Ollama 時也能在 log 直接看到原因與修正方式。
結果存在 STATUS，/health 會一併回傳。
"""

from __future__ import annotations

import os

import httpx

# ready 用英文布林值，讓 demo 腳本不必比對中文（Windows PowerShell 5.1 解碼 JSON 時會把中文弄亂）
STATUS: dict = {"ready": False, "ollama": "未檢查"}

FIX_HINT = (
    "容器連不到主機的 Ollama（{url}）。請確認：\n"
    "  1. 主機上 Ollama 已啟動（工作列有 Ollama 圖示，或執行 ollama serve）\n"
    "  2. Windows／Linux 需讓 Ollama 接受外部連線：設定環境變數 OLLAMA_HOST=0.0.0.0 後重啟 Ollama\n"
    "     （repo 根目錄的 demo.ps1／demo.sh 會自動處理）"
)


def _have(name: str, installed: set[str]) -> bool:
    return name in installed or f"{name}:latest" in installed


def ensure_models(url: str, models: list[str]) -> dict:
    auto_pull = os.environ.get("OLLAMA_AUTO_PULL", "1").strip() != "0"
    try:
        tags = httpx.get(f"{url}/api/tags", timeout=5).json()
    except Exception as e:  # noqa: BLE001
        print(f"[ollama] 連線失敗：{type(e).__name__}: {e}\n{FIX_HINT.format(url=url)}", flush=True)
        STATUS.update(ready=False, ollama="連不到", url=url, missing=models)
        return STATUS

    installed = {m["name"] for m in tags.get("models", [])}
    missing = [m for m in models if not _have(m, installed)]
    for m in list(missing):
        if not auto_pull:
            break
        print(f"[ollama] 缺少模型 {m}，自動下載中（只有第一次需要，視網路約數分鐘）…", flush=True)
        try:
            httpx.post(f"{url}/api/pull", json={"model": m, "stream": False}, timeout=None).raise_for_status()
            missing.remove(m)
            print(f"[ollama] {m} 下載完成", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[ollama] {m} 下載失敗：{e}；請在主機執行 ollama pull {m}", flush=True)

    STATUS.update(ready=not missing, ollama="正常" if not missing else "缺少模型", url=url, models=models, missing=missing)
    print(f"[ollama] {STATUS}", flush=True)
    return STATUS
