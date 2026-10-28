import argparse
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_ROOT = PROJECT_ROOT.parent / "frontend"
FRONTEND_DIST = FRONTEND_ROOT / "dist"


def _frontend_needs_build():
    index_path = FRONTEND_DIST / "index.html"
    if not index_path.exists():
        return True

    build_time = index_path.stat().st_mtime
    source_paths = [
        FRONTEND_ROOT / "index.html",
        FRONTEND_ROOT / "package.json",
        FRONTEND_ROOT / "vite.config.js",
        *list((FRONTEND_ROOT / "src").rglob("*")),
    ]
    return any(
        path.is_file() and path.stat().st_mtime > build_time for path in source_paths
    )


def _build_frontend():
    if not _frontend_needs_build():
        return

    if not (FRONTEND_ROOT / "node_modules").exists():
        raise SystemExit(
            "프론트엔드 의존성이 없습니다. 먼저 저장소 루트에서 `npm --prefix frontend ci`를 실행해 주세요."
        )

    print("Svelte 프론트엔드를 빌드합니다...")
    try:
        subprocess.run(
            ["npm", "run", "build"],
            cwd=FRONTEND_ROOT,
            check=True,
        )
    except FileNotFoundError as error:
        raise SystemExit("Node.js와 npm을 설치한 뒤 다시 실행해 주세요.") from error
    except subprocess.CalledProcessError as error:
        raise SystemExit("Svelte 프론트엔드 빌드에 실패했습니다.") from error


def _parse_args():
    parser = argparse.ArgumentParser(description="Placepick 로컬 웹 애플리케이션")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="백엔드 자동 새로고침")
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Svelte 변경 확인 및 자동 빌드를 건너뜁니다.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    if not args.no_build:
        _build_frontend()

    import uvicorn

    print(f"Placepick: http://{args.host}:{args.port}")
    uvicorn.run(
        "blog_place_collector.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
