#!/usr/bin/env python3
"""Live Blender modeling loop — cloud code LLM builds, design LLM reviews.

Architecture (one iteration):

    1. Builder LLM writes Blender Python for the brief.
       --builder claude  : Claude (default — strongest bpy knowledge, fewest retries)
       --builder gemini  : Gemini Flash (free tier)
       --builder ollama  : local Ollama model (experimental; needs >8GB machines)
    2. The script runs inside the live Blender via the BlenderMCP addon's TCP
       socket (port 9876). Python errors are fed back for self-debugging.
    3. The scene is rendered to PNG.
    4. Reviewer LLM (Gemini vision) scores the render against the brief and
       writes concrete revision instructions.
    5. Below the score threshold, the feedback loops back to step 1.

Prerequisites:
    * Blender running with the BlenderMCP addon connected (port 9876).
    * For --builder claude: ANTHROPIC_API_KEY in .env (pay-as-you-go credit)
    * For --builder gemini: GEMINI_API_KEY in .env
    * For --builder ollama: Ollama running with model pulled

Usage::

    # Default: Claude builder + Gemini vision review
    python examples/run_blender_live.py "丸みを帯びたワイヤレスキーボードのコンセプトモデル"

    # Free-tier builder
    python examples/run_blender_live.py --builder gemini "..."

    # Skip design review (no GEMINI_API_KEY needed)
    python examples/run_blender_live.py --no-review "..."
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Protocol

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_env
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.gemini_vision import GeminiVisionReviewer
from src.llm.ollama_provider import OllamaProvider
from src.llm.provider import ProviderConfig
from src.mcp.blender_tcp import BlenderTcpClient
from src.models import LlmMessage

_BUILDER_DEFAULT_MODELS = {
    "claude": "claude-opus-4-8",
    "gemini": "gemini-2.0-flash",
    "ollama": "qwen2.5-coder:7b",
}

# ── Worker prompts ──────────────────────────────────────────────────────── #

_WORKER_SYSTEM = """You are an expert Blender Python (bpy) developer.
You write scripts that build 3D concept models in a LIVE Blender session.

STRICT RULES:
1. Output ONLY Python code. No markdown fences, no explanations, no comments in other languages.
2. The script must begin with these exact imports (always needed):
   import bpy, math, mathutils
   from mathutils import Vector, Matrix, Euler
3. START by deleting all existing MESH objects (keep Camera and Light objects):
   for obj in [o for o in bpy.data.objects if o.type == 'MESH']:
       bpy.data.objects.remove(obj, do_unlink=True)
4. NEVER delete or rename Camera or Light objects. NEVER call render functions.
5. Build the model centered near the world origin, fitting within roughly a 4x4x4 unit box.
6. Create simple PRINCIPLED materials (base color, metallic, roughness) and assign them.
7. Use primitives (bpy.ops.mesh.primitive_*), modifiers (Bevel, Subdivision) and
   simple loops for repeated parts (keys, buttons, vents).
8. Keep the whole script under 120 lines and make it runnable top-to-bottom without errors.

CRITICAL API RULES — violating these causes AttributeError:
- obj.type tells you the object type: 'MESH', 'LIGHT', 'CAMERA', etc.
- Mesh data (obj.data when obj.type=='MESH') has: vertices, edges, faces, polygons — NOT energy.
- Light data (obj.data when obj.type=='LIGHT') has: energy, color — NOT vertices.
- To set light brightness: light_obj.data.energy = 5.0  (NOT light_obj.energy)
- NEVER write obj.energy or obj.data.energy unless you confirmed obj.type == 'LIGHT'.
- All Vector() calls must use 3 components: Vector((x, y, z)) — NOT Vector((x, y)).

CORRECT material setup:
  mat = bpy.data.materials.new("Mat")
  mat.use_nodes = True
  bsdf = mat.node_tree.nodes["Principled BSDF"]
  bsdf.inputs["Base Color"].default_value = (0.2, 0.2, 0.2, 1.0)
  bsdf.inputs["Metallic"].default_value = 0.8
  bsdf.inputs["Roughness"].default_value = 0.3
  obj.data.materials.append(mat)"""

_FIX_TEMPLATE = """Your previous Blender script raised an error.

# Previous script
{code}

# Error
{error}

Output the FULL corrected script (not a diff). Remember: code only, no fences.
Start with: import bpy, math, mathutils"""

_REVISE_TEMPLATE = """# Design brief
{brief}

# Previous script (iteration {iteration})
{code}

# Art director's review of the rendered result (score: {score}/100)
{feedback}

Revise the model to address every point in the review.
Output the FULL new script (not a diff). Remember: code only, no fences.
Start with: import bpy, math, mathutils"""

_INITIAL_TEMPLATE = """# Design brief
{brief}

Write a Blender Python script that builds this as a presentable 3D concept model.
Start with: import bpy, math, mathutils"""

# ── Render helper ───────────────────────────────────────────────────────── #

_RENDER_SCRIPT = """import bpy
from mathutils import Vector
scene = bpy.context.scene
if scene.camera is None:
    cam_data = bpy.data.cameras.new('AutoCam')
    cam = bpy.data.objects.new('AutoCam', cam_data)
    scene.collection.objects.link(cam)
    cam.location = (7.0, -7.0, 5.0)
    direction = Vector((0.0, 0.0, 0.5)) - cam.location
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    scene.camera = cam
if not any(o.type == 'LIGHT' for o in scene.objects):
    light_data = bpy.data.lights.new('AutoSun', 'SUN')
    light = bpy.data.objects.new('AutoSun', light_data)
    scene.collection.objects.link(light)
    light.location = (4.0, -4.0, 8.0)
scene.render.resolution_x = 960
scene.render.resolution_y = 720
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = {path!r}
bpy.ops.render.render(write_still=True)
"""

# ── Code builder protocol ───────────────────────────────────────────────── #

class CodeBuilder(Protocol):
    async def generate(self, system: str, user: str) -> str: ...


class ClaudeCodeBuilder:
    """Cloud script builder — Anthropic Messages API via the official SDK.

    Wraps the project's AnthropicProvider (same pattern as OllamaCodeBuilder);
    the SDK auto-retries 429/529 with exponential backoff.
    """

    def __init__(self, api_key: str, model: str = "claude-opus-4-8"):
        self._provider = AnthropicProvider(
            ProviderConfig(
                provider="anthropic",
                model=model,
                api_key=api_key,
                max_tokens=4096,
                temperature=0.2,
            )
        )

    async def generate(self, system: str, user: str) -> str:
        messages = [
            LlmMessage(role="system", content=system),
            LlmMessage(role="user", content=user),
        ]
        response = await self._provider.complete(messages)
        return response.text


class GeminiCodeBuilder:
    """Cloud script builder — Gemini REST API (httpx, no SDK required)."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self._api_key = api_key
        self._model = model
        self._url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )

    async def generate(self, system: str, user: str) -> str:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": system + "\n\n" + user}]}],
            "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.2},
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            last_err = ""
            for attempt in range(3):
                try:
                    resp = await client.post(
                        self._url, params={"key": self._api_key}, json=payload
                    )
                    if resp.status_code == 429:
                        last_err = f"HTTP 429 (quota exceeded)"
                        wait = 2 ** attempt * 5
                        print(f"    Gemini rate limit — {wait}秒待機...")
                        await asyncio.sleep(wait)
                        continue
                    if resp.status_code != 200:
                        last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                        raise RuntimeError(last_err)
                    data = resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except RuntimeError:
                    raise
                except Exception as exc:
                    last_err = str(exc)
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 ** attempt)
        raise RuntimeError(f"Gemini API failed after 3 retries: {last_err}")


class OllamaCodeBuilder:
    """Local script builder using Ollama."""

    def __init__(self, provider: OllamaProvider):
        self._provider = provider

    async def generate(self, system: str, user: str) -> str:
        messages = [
            LlmMessage(role="system", content=system),
            LlmMessage(role="user", content=user),
        ]
        response = await self._provider.complete(messages)
        return response.text


# ── Code post-processing ────────────────────────────────────────────────── #

_REQUIRED_IMPORTS = (
    "import bpy\n"
    "import math\n"
    "import mathutils\n"
    "from mathutils import Vector, Matrix, Euler\n"
    "try:\n    import bmesh\nexcept ImportError:\n    pass\n\n"
)


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    match = re.search(r"```(?:python)?\s*\n(.*?)```", stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def _sanitize_bpy_code(code: str) -> str:
    """Fix common bpy API mistakes and guarantee required imports."""
    # Strip existing import lines, then prepend canonical block.
    lines = [
        l for l in code.splitlines()
        if not re.match(r"^\s*import\s+(bpy|math|mathutils|bmesh)\b", l)
        and not re.match(r"^\s*from\s+(mathutils|bmesh)\b", l)
    ]
    code = _REQUIRED_IMPORTS + "\n".join(lines)

    # Guard .energy assignments (only valid on Light datablocks).
    code = re.sub(
        r"^(\s*)(\w+)\.energy(\s*=)",
        r'\1if hasattr(\2, "energy"): \2.energy\3',
        code, flags=re.MULTILINE,
    )
    code = re.sub(
        r"^(\s*)(\w+)\.data\.energy(\s*=)",
        r'\1if hasattr(\2.data, "energy"): \2.data.energy\3',
        code, flags=re.MULTILINE,
    )

    # Upgrade 2-component Vector() to 3-component.
    def _fix_vector2d(m: re.Match) -> str:
        inner = m.group(1).strip()
        depth = commas = 0
        for ch in inner:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "," and depth == 0:
                commas += 1
        return f"Vector(({inner}, 0.0))" if commas == 1 else m.group(0)

    code = re.sub(r"Vector\(\(([^()]+)\)\)", _fix_vector2d, code)

    # Fix common enum value mistakes small models emit.
    _ENUM_FIXES = [
        # space.perspective / region_3d.view_perspective
        (r'"PERSPECTIVE"', '"PERSP"'),
        (r"'PERSPECTIVE'", "'PERSP'"),
        # object display types
        (r'"SOLID_WIRE"', '"SOLID"'),
        # shading types
        (r'"MATERIAL_PREVIEW"', '"MATERIAL"'),
        # curve fill modes
        (r'\.fill_mode\s*=\s*"FULL"', '.fill_mode = "FRONT"'),
    ]
    for pattern, replacement in _ENUM_FIXES:
        code = re.sub(pattern, replacement, code)

    return code


# ── Preflight checks ────────────────────────────────────────────────────── #

async def _check_blender(host: str, port: int) -> BlenderTcpClient:
    client = BlenderTcpClient(host=host, port=port, timeout=300.0)
    try:
        await client.connect()
        info = await client.call_tool("get_scene_info", {})
    except Exception as exc:
        raise SystemExit(
            f"❌ Blenderに接続できません ({host}:{port}): {exc}\n"
            "   BlenderMCPアドオンのパネルから 'Connect to MCP server' を押して\n"
            "   ポート9876でサーバーが起動しているか確認してください。"
        ) from exc
    if not info.ok:
        raise SystemExit(f"❌ Blenderシーン情報の取得に失敗: {info.error}")
    objects = (info.content or {}).get("object_count", "?")
    print(f" 🟢 Blender接続OK ({host}:{port}) — シーン内オブジェクト: {objects}")
    return client


async def _check_ollama(base_url: str, model: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
            tags = resp.json()
    except Exception as exc:
        raise SystemExit(
            f"❌ Ollamaに接続できません ({base_url}): {exc}\n"
            "   Ollamaアプリを起動するか `ollama serve` を実行してください。\n"
            "   未インストールの場合: https://ollama.com/download"
        ) from exc
    names = [m.get("name", "") for m in tags.get("models", [])]
    if model not in names:
        raise SystemExit(
            f"❌ Ollamaにモデル {model!r} がありません（現在: {names or 'なし'}）。\n"
            f"   `ollama pull {model}` を実行してください。"
        )
    print(f" 🟢 Ollama接続OK — ワーカーモデル: {model}")


# ── Main loop ───────────────────────────────────────────────────────────── #

async def _generate_code(builder: CodeBuilder, user_prompt: str) -> str:
    raw = await builder.generate(_WORKER_SYSTEM, user_prompt)
    return _sanitize_bpy_code(_strip_code_fences(raw))


async def _execute_with_debug(
    blender: BlenderTcpClient,
    builder: CodeBuilder,
    code: str,
    max_fixes: int = 2,
) -> tuple[str, bool]:
    """Run code in Blender; on Python errors the builder self-debugs."""
    for attempt in range(max_fixes + 1):
        result = await blender.call_tool("execute_blender_code", {"code": code})
        if result.ok:
            return code, True
        error = result.error or "unknown error"
        if attempt == max_fixes:
            print(f" ⚠️ Blender実行エラー (最終試行): {error[:160]}")
            break
        print(f" ⚠️ Blender実行エラー (修正 {attempt + 1}/{max_fixes}): {error[:120]}")
        code = await _generate_code(builder, _FIX_TEMPLATE.format(code=code, error=error))
    return code, False


async def _main(args: argparse.Namespace) -> int:
    load_env()

    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Blenderライブモデリングテスト ===")
    print(f"要件: {args.requirement}\n")

    blender = await _check_blender(args.host, args.port)

    # Build the code-generation backend.
    builder: CodeBuilder
    builder_model = args.builder_model or _BUILDER_DEFAULT_MODELS[args.builder]

    if args.builder == "claude":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise SystemExit(
                "❌ ANTHROPIC_API_KEY が未設定です。\n"
                "   https://console.anthropic.com でAPIキーを発行（従量課金・クレジット購入要）し\n"
                "   .env に ANTHROPIC_API_KEY=sk-ant-... を追記してください。\n"
                "   無料枠で試す場合は --builder gemini を指定してください。"
            )
        builder = ClaudeCodeBuilder(api_key=api_key, model=builder_model)
        print(f" 🟢 ビルダー: Claude ({builder_model}) — クラウドLLM（最高精度）")
    elif args.builder == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise SystemExit("❌ GEMINI_API_KEY が未設定です。.env に設定してください。")
        builder = GeminiCodeBuilder(api_key=api_key, model=builder_model)
        print(f" 🟢 ビルダー: Gemini ({builder_model}) — クラウドLLM（無料枠）")
    else:
        # Experimental: needs a machine with enough memory for a capable model.
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        await _check_ollama(ollama_url, builder_model)
        builder = OllamaCodeBuilder(
            OllamaProvider(
                ProviderConfig(
                    provider="ollama",
                    model=builder_model,
                    base_url=ollama_url,
                    max_tokens=2048,
                    temperature=0.2,
                )
            )
        )
        print(f" 🟢 ビルダー: Ollama ({builder_model}) — ローカルLLM（実験用）")

    reviewer: GeminiVisionReviewer | None = None
    if not args.no_review:
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if not gemini_key:
            raise SystemExit(
                "❌ GEMINI_API_KEY が未設定です。.env に設定するか --no-review を付けてください。"
            )
        reviewer = GeminiVisionReviewer(
            api_key=gemini_key,
            model=args.reviewer_model,
            score_threshold=args.score_threshold,
        )
        print(f" 🟢 レビューア: {args.reviewer_model} (合格スコア: {args.score_threshold})")

    code = ""
    history: list[dict] = []
    try:
        for iteration in range(1, args.iterations + 1):
            print(f"\n── イテレーション {iteration}/{args.iterations} ──")

            if iteration == 1:
                prompt = _INITIAL_TEMPLATE.format(brief=args.requirement)
            else:
                prev = history[-1]
                prompt = _REVISE_TEMPLATE.format(
                    brief=args.requirement,
                    iteration=iteration - 1,
                    code=code,
                    score=prev["score"],
                    feedback=prev["feedback"],
                )

            builder_label = "ローカルLLM" if args.builder == "ollama" else "クラウドLLM"
            print(f" 🧠 {builder_label}がBlenderスクリプトを生成中...")
            code = await _generate_code(builder, prompt)
            print(f"    生成: {len(code.splitlines())}行")

            print(" 🛠️ Blenderで実行中...")
            code, ok = await _execute_with_debug(blender, builder, code)
            if not ok:
                print(" ❌ スクリプトを修正しきれませんでした。次のイテレーションへ。")
                history.append({"score": 0, "feedback": "スクリプト実行エラー", "render": None})
                continue
            print(" ✅ モデリング完了")

            render_path = str(outdir / f"iter_{iteration:02d}.png")
            print(" 📷 レンダリング中...")
            render = await blender.call_tool(
                "execute_blender_code", {"code": _RENDER_SCRIPT.format(path=render_path)}
            )
            if not render.ok:
                print(f" ❌ レンダリング失敗: {render.error}")
                history.append({"score": 0, "feedback": "レンダリング失敗", "render": None})
                continue
            print(f"    保存: {render_path}")

            if reviewer is None:
                history.append({"score": -1, "feedback": "(レビューなし)", "render": render_path})
                print(" ℹ️ --no-review のためレビューをスキップしました。")
                break

            print(" 🎨 デザインLLMがレビュー中...")
            verdict = await reviewer.review(args.requirement, render_path)
            verdict["render"] = render_path
            history.append(verdict)
            print(f"    スコア: {verdict['score']}/100")
            print(f"    フィードバック: {verdict['feedback']}")

            if verdict["approved"]:
                print(f"\n 🏆 合格（スコア {verdict['score']} ≥ {args.score_threshold}）")
                break
    finally:
        await blender.close()

    print("\n" + "=" * 60)
    for i, entry in enumerate(history, start=1):
        score = "—" if entry["score"] < 0 else f"{entry['score']:>3}"
        print(f"  iter {i}: score {score}  {entry.get('render') or '(レンダリングなし)'}")
    final = next((h for h in reversed(history) if h.get("render")), None)
    if final:
        print(f"\n最終レンダリング: {final['render']}")
        print(f"画像を開く:       open {final['render']}")
    print("=" * 60)
    return 0 if final else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live Blender modeling with cloud or local LLM + design review."
    )
    parser.add_argument(
        "requirement",
        nargs="?",
        default="丸みを帯びたミニマルデザインのコンパクトなワイヤレスキーボードのコンセプトモデル",
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument(
        "--builder", choices=["claude", "gemini", "ollama"], default="claude",
        help="LLM backend for bpy script generation "
             "(claude=default/highest accuracy, gemini=free tier, ollama=local experiment)",
    )
    parser.add_argument(
        "--builder-model", default=None,
        help="Model name override (defaults: claude=claude-opus-4-8, "
             "gemini=gemini-2.0-flash, ollama=qwen2.5-coder:7b)",
    )
    parser.add_argument("--reviewer-model", default="gemini-2.0-flash")
    parser.add_argument("--score-threshold", type=int, default=75)
    parser.add_argument("--no-review", action="store_true")
    parser.add_argument("--outdir", default="outputs/blender_live")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
