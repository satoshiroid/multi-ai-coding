#!/usr/bin/env python3
"""Live Blender modeling loop — local code LLM builds, design LLM reviews.

Architecture (one iteration):

    1. Worker LLM (local Ollama, default qwen2.5-coder) writes Blender Python
       for the brief.
    2. The script runs inside the live Blender via the BlenderMCP addon's TCP
       socket (port 9876). Python errors are fed back for self-debugging.
    3. The scene is rendered to PNG.
    4. Reviewer LLM (Gemini vision) scores the render against the brief and
       writes concrete revision instructions.
    5. Below the score threshold, the feedback loops back to step 1.

Prerequisites:
    * Blender running with the BlenderMCP addon connected (port 9876).
    * Ollama running with the worker model pulled
      (`ollama pull qwen2.5-coder:7b`).
    * GEMINI_API_KEY in .env (skip review entirely with --no-review).

Usage::

    python examples/run_blender_live.py "丸みを帯びたワイヤレスキーボードのコンセプトモデル"
    python examples/run_blender_live.py --iterations 2 --worker-model qwen2.5-coder:3b "..."
    python examples/run_blender_live.py --no-review "..."   # GEMINI_API_KEY不要
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_env
from src.llm.gemini_vision import GeminiVisionReviewer
from src.llm.ollama_provider import OllamaProvider
from src.llm.provider import ProviderConfig
from src.mcp.blender_tcp import BlenderTcpClient
from src.models import LlmMessage

# ── Worker (local LLM) prompts ──────────────────────────────────────────── #

_WORKER_SYSTEM = """You are an expert Blender Python (bpy) developer.
You write scripts that build 3D concept models in a LIVE Blender session.

STRICT RULES:
1. Output ONLY Python code. No markdown fences, no explanations, no comments in other languages.
2. Use only these imports: bpy, math, mathutils, bmesh.
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
- Mesh data (obj.data when obj.type=='MESH') has: vertices, edges, faces, polygons — NOT energy/color/shadow.
- Light data (obj.data when obj.type=='LIGHT') has: energy, color, shadow_soft_size — NOT vertices/faces.
- To set light brightness: light_obj.data.energy = 5.0  (NOT light_obj.energy)
- To make an emission material, use a ShaderNodeEmission on a material, do NOT set mesh.energy.
- bpy.data.objects[name] returns an Object; accessing .data gives the object's datablock (Mesh or Light etc).
- NEVER write obj.energy unless you have confirmed obj.type == 'LIGHT'.

CORRECT material setup example:
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

Output the FULL corrected script (not a diff). Remember: code only, no fences."""

_REVISE_TEMPLATE = """# Design brief
{brief}

# Previous script (iteration {iteration})
{code}

# Art director's review of the rendered result (score: {score}/100)
{feedback}

Revise the model to address every point in the review.
Output the FULL new script (not a diff). Remember: code only, no fences."""

_INITIAL_TEMPLATE = """# Design brief
{brief}

Write a Blender Python script that builds this as a presentable 3D concept model."""

# ── Render helper (runs inside Blender via execute_code) ────────────────── #

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


def _strip_code_fences(text: str) -> str:
    """Remove ```python fences if the model added them despite instructions."""
    stripped = text.strip()
    match = re.search(r"```(?:python)?\s*\n(.*?)```", stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


_REQUIRED_IMPORTS = "import bpy\nimport math\nimport mathutils\nfrom mathutils import Vector, Matrix, Euler\ntry:\n    import bmesh\nexcept ImportError:\n    pass\n\n"


def _sanitize_bpy_code(code: str) -> str:
    """Fix common small-model bpy API mistakes before sending to Blender."""
    # Ensure required imports are present regardless of what the model emits.
    # Strip any existing import lines first, then prepend a canonical block.
    lines = [l for l in code.splitlines()
             if not re.match(r'^\s*import\s+(bpy|math|mathutils|bmesh)', l)
             and not re.match(r'^\s*from\s+(mathutils|bmesh)', l)]
    code = _REQUIRED_IMPORTS + "\n".join(lines)

    # obj.energy = X  →  guarded (energy only exists on Light datablocks)
    code = re.sub(
        r'^(\s*)(\w+)\.energy(\s*=)',
        r'\1if hasattr(\2, "energy"): \2.energy\3',
        code,
        flags=re.MULTILINE,
    )
    # obj.data.energy = X  →  guarded
    code = re.sub(
        r'^(\s*)(\w+)\.data\.energy(\s*=)',
        r'\1if hasattr(\2.data, "energy"): \2.data.energy\3',
        code,
        flags=re.MULTILINE,
    )
    # Normalise mathutils.Vector calls: Vector((x, y)) → Vector((x, y, 0))
    # Small models sometimes emit 2-component vectors in 3-D contexts.
    def _fix_vector2d(m: re.Match) -> str:
        inner = m.group(1).strip()
        # Count top-level commas (not inside nested parens/brackets)
        depth = 0
        commas = 0
        for ch in inner:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "," and depth == 0:
                commas += 1
        if commas == 1:
            return f"Vector(({inner}, 0.0))"
        return m.group(0)

    code = re.sub(r"Vector\(\(([^()]+)\)\)", _fix_vector2d, code)
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
            "   Blender側でBlenderMCPアドオンのパネルから 'Connect to MCP server' を\n"
            "   押してポート9876でサーバーが起動しているか確認してください。"
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

async def _generate_code(worker: OllamaProvider, user_prompt: str) -> str:
    messages = [
        LlmMessage(role="system", content=_WORKER_SYSTEM),
        LlmMessage(role="user", content=user_prompt),
    ]
    response = await worker.complete(messages)
    code = _strip_code_fences(response.text)
    return _sanitize_bpy_code(code)


async def _execute_with_debug(
    blender: BlenderTcpClient, worker: OllamaProvider, code: str, max_fixes: int = 2
) -> tuple[str, bool]:
    """Run code in Blender; on Python errors let the worker self-debug."""
    for attempt in range(max_fixes + 1):
        result = await blender.call_tool("execute_blender_code", {"code": code})
        if result.ok:
            return code, True
        error = result.error or "unknown error"
        print(f" ⚠️ Blender実行エラー (修正 {attempt + 1}/{max_fixes}): {error[:200]}")
        if attempt == max_fixes:
            return code, False
        code = await _generate_code(
            worker, _FIX_TEMPLATE.format(code=code, error=error)
        )
    return code, False  # pragma: no cover


async def _main(args: argparse.Namespace) -> int:
    load_env()

    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Blenderライブモデリングテスト ===")
    print(f"要件: {args.requirement}\n")

    # Preflight: all three actors must be reachable before we burn any tokens.
    blender = await _check_blender(args.host, args.port)
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    await _check_ollama(ollama_url, args.worker_model)

    reviewer: GeminiVisionReviewer | None = None
    if not args.no_review:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise SystemExit(
                "❌ GEMINI_API_KEY が未設定です。.env に設定するか --no-review を付けてください。"
            )
        reviewer = GeminiVisionReviewer(
            api_key=api_key,
            model=args.reviewer_model,
            score_threshold=args.score_threshold,
        )
        print(f" 🟢 レビューア: {args.reviewer_model} (合格スコア: {args.score_threshold})")

    worker = OllamaProvider(
        ProviderConfig(
            provider="ollama",
            model=args.worker_model,
            base_url=ollama_url,
            max_tokens=2048,
            temperature=0.2,
        )
    )

    code = ""
    history: list[dict] = []
    try:
        for iteration in range(1, args.iterations + 1):
            print(f"\n── イテレーション {iteration}/{args.iterations} ──")

            # 1. Code generation (initial / revision).
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
            print(" 🧠 ローカルLLMがBlenderスクリプトを生成中...")
            code = await _generate_code(worker, prompt)
            print(f"    生成: {len(code.splitlines())}行")

            # 2. Execute in the live Blender (with self-debug retries).
            print(" 🛠️ Blenderで実行中...")
            code, ok = await _execute_with_debug(blender, worker, code)
            if not ok:
                print(" ❌ スクリプトを修正しきれませんでした。次のイテレーションへ。")
                history.append({"score": 0, "feedback": "スクリプト実行エラー", "render": None})
                continue
            print(" ✅ モデリング完了")

            # 3. Render.
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

            # 4. Design review.
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

    # Summary.
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
    parser = argparse.ArgumentParser(description="Live Blender modeling with local LLM + design review.")
    parser.add_argument(
        "requirement",
        nargs="?",
        default="丸みを帯びたミニマルデザインのコンパクトなワイヤレスキーボードのコンセプトモデル",
    )
    parser.add_argument("--host", default="localhost", help="BlenderMCP addon host")
    parser.add_argument("--port", type=int, default=9876, help="BlenderMCP addon port")
    parser.add_argument("--iterations", type=int, default=3, help="max build→review cycles")
    parser.add_argument(
        "--worker-model", default="qwen2.5-coder:7b",
        help="Ollama model that writes Blender Python (3b is faster on small machines)",
    )
    parser.add_argument("--reviewer-model", default="gemini-2.0-flash", help="vision review model")
    parser.add_argument("--score-threshold", type=int, default=75, help="passing review score")
    parser.add_argument("--no-review", action="store_true", help="skip the design review step")
    parser.add_argument("--outdir", default="outputs/blender_live", help="render output directory")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
