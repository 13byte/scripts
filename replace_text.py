import os
import logging
import hashlib
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from datetime import datetime
import time
from tqdm import tqdm
import threading
from typing import List, Tuple, Dict


class LogManager:
    """로그 관리를 위한 클래스"""

    def __init__(
        self, script_name: str, log_dir: str = "./script_logs", max_logs: int = 50
    ):
        self.script_name = script_name
        self.log_dir = Path(log_dir)
        self.max_logs = max_logs
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"{self.timestamp}_{self.script_name}.py.log"

        self.setup_logging()
        self.cleanup_old_logs()

    def setup_logging(self):
        """로깅 설정 및 초기화"""
        # 로그 디렉토리 생성
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 로깅 설정
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(), logging.FileHandler(self.log_file)],
        )

        # 초기 로그 정보 기록
        logging.info("-" * 50)
        logging.info(
            f"Script started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logging.info(f"Working directory: {os.getcwd()}")
        logging.info("-" * 50)

    def cleanup_old_logs(self):
        """오래된 로그 파일 정리"""
        try:
            log_files = sorted(
                self.log_dir.glob(f"*_{self.script_name}.log"),
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            )

            if len(log_files) > self.max_logs:
                for old_log in log_files[self.max_logs :]:
                    old_log.unlink()
                logging.info(
                    f"Cleaned up {len(log_files) - self.max_logs} old log files"
                )
        except Exception as e:
            logging.error(f"Error during log cleanup: {e}")

    def finalize(self):
        """로깅 종료 처리"""
        logging.info("-" * 50)
        logging.info(
            f"Script finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logging.info("-" * 50)


# 처리할 텍스트 파일 확장자
# TEXT_FILE_EXTENSIONS = {'.ini', '.conf'}
TEXT_FILE_EXTENSIONS = {".ini"}

# 변경할 문자열 정의
OLD_TEXT = r'"Color Scheme"=Solarized Dark'
NEW_TEXT = r'"Color Scheme"=Tomorrow'


def calculate_file_hash(file_path: Path) -> str:
    """파일의 SHA-256 해시값을 계산"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def preview_changes(
    file_path: Path, old_text: str, new_text: str
) -> Tuple[bool, List[Tuple[int, str, str]]]:
    """파일의 변경 사항을 미리 확인"""
    changes = []
    need_change = False

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if old_text in line:
                    need_change = True
                    changes.append(
                        (i, line.strip(), line.replace(old_text, new_text).strip())
                    )
    except UnicodeDecodeError:
        logging.warning(f"인코딩 오류, 건너뜀: {file_path}")
    except Exception as e:
        logging.error(f"파일 검사 중 오류 발생: {file_path}", exc_info=True)

    return need_change, changes


def process_file(
    file_path: Path, old_text: str, new_text: str, preview_only: bool = False
) -> Dict:
    """단일 파일 처리"""
    result = {
        "path": str(file_path),
        "modified": False,
        "changes": [],
        "error": None,
        "original_hash": None,
        "new_hash": None,
    }

    if file_path.name == "replace_text.py":
        return result

    if file_path.suffix not in TEXT_FILE_EXTENSIONS:
        return result

    try:
        need_change, changes = preview_changes(file_path, old_text, new_text)
        result["changes"] = changes

        if need_change and not preview_only:
            result["original_hash"] = calculate_file_hash(file_path)

            temp_path = file_path.with_suffix(file_path.suffix + ".tmp")
            backup_path = file_path.with_suffix(file_path.suffix + ".bak")

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            modified_content = content.replace(old_text, new_text)

            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(modified_content)

            os.replace(file_path, backup_path)
            os.replace(temp_path, file_path)

            result["new_hash"] = calculate_file_hash(file_path)
            result["modified"] = True

            logging.info(f"파일 변경 완료: {file_path}")
            logging.info(f"원본 해시: {result['original_hash']}")
            logging.info(f"새 해시: {result['new_hash']}")

    except Exception as e:
        result["error"] = str(e)
        logging.error(f"파일 처리 중 오류 발생: {file_path}", exc_info=True)

    return result


def collect_files(directory: str) -> List[Path]:
    """처리할 파일 목록 수집"""
    return [p for p in Path(directory).rglob("*") if p.suffix in TEXT_FILE_EXTENSIONS]


def replace_in_files(directory: str, preview_only: bool = False) -> Dict:
    """디렉토리 내의 모든 파일에서 텍스트를 변경"""
    try:
        all_files = collect_files(directory)
        total_files = len(all_files)

        logging.info(f"처리할 파일 수: {total_files}")

        results = []
        with tqdm(total=total_files, desc="파일 처리 중") as pbar:
            with ProcessPoolExecutor() as executor:
                process_func = partial(
                    process_file,
                    old_text=OLD_TEXT,
                    new_text=NEW_TEXT,
                    preview_only=preview_only,
                )

                futures = []
                for file_path in all_files:
                    future = executor.submit(process_func, file_path)
                    future.add_done_callback(lambda p: pbar.update(1))
                    futures.append(future)

                for future in futures:
                    results.append(future.result())

        stats = {
            "total_files": total_files,
            "modified_files": sum(1 for r in results if r["modified"]),
            "error_files": sum(1 for r in results if r["error"]),
            "preview_only": preview_only,
            "changes": [r for r in results if r["changes"]],
            "errors": [r for r in results if r["error"]],
        }

        logging.info("처리 완료:")
        logging.info(f"- 전체 파일 수: {stats['total_files']}")
        logging.info(f"- 수정된 파일 수: {stats['modified_files']}")
        logging.info(f"- 오류 발생 파일 수: {stats['error_files']}")

        return stats

    except Exception as e:
        logging.error("처리 중 오류 발생", exc_info=True)
        return {
            "total_files": 0,
            "modified_files": 0,
            "error_files": 1,
            "preview_only": preview_only,
            "changes": [],
            "errors": [str(e)],
        }


if __name__ == "__main__":
    script_name = Path(__file__).stem
    log_manager = LogManager(script_name)

    current_dir = os.getcwd()
    logging.info(f"시작 디렉토리: {current_dir}")

    # 미리보기 실행
    print("\n미리보기 모드로 실행 중...")
    preview_stats = replace_in_files(current_dir, preview_only=True)

    if preview_stats["changes"]:
        print("\n다음 파일들이 변경될 예정입니다:")
        for change in preview_stats["changes"]:
            print(f"\n파일: {change['path']}")
            for line_num, old, new in change["changes"]:
                print(f"  줄 {line_num}:")
                print(f"    전: {old}")
                print(f"    후: {new}")

    response = input("\n실제로 파일들을 수정하시겠습니까? (y/n): ")

    if response.lower() == "y":
        print("\n파일 수정 중...")
        stats = replace_in_files(current_dir)

        if stats["modified_files"] > 0:
            print(f"\n성공적으로 {stats['modified_files']}개 파일이 수정되었습니다.")
            print("자세한 내용은 로그 파일을 확인하세요.")

        if stats["error_files"] > 0:
            print(f"\n주의: {stats['error_files']}개 파일에서 오류가 발생했습니다.")
            print("자세한 내용은 로그 파일을 확인하세요.")
    else:
        logging.info("작업이 취소되었습니다.")

    log_manager.finalize()
