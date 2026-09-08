"""Start from the project root: uv run run.py"""
import argparse
import uvicorn

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='로컬 카드뉴스 작업실')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    print(f'브라우저에서 http://127.0.0.1:{args.port} 를 여세요.')
    uvicorn.run('app:app', host='127.0.0.1', port=args.port)
