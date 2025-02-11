#!/usr/bin/env python3
import os
import signal
import subprocess
import glob
import re
import sys
from typing import List, Dict, Tuple, Optional


class MariaDBError(Exception):
    """MariaDB 관련 기본 예외"""

    pass


class Constants:
    """상수 정의 클래스"""

    MAX_ATTEMPTS = 3
    DEFAULT_SOCKET_PATH = "/var/lib/mysql/mysql.sock"
    OLD_SOCKET_PATH = "/var/run/mysqld/mysqld.sock"
    CNF_DIR = "cnf"  # 기본 설정 디렉토리
    MY_CNF = os.path.join(CNF_DIR, "my.cnf")  # my.cnf 파일
    MYCNF_D_DIR = os.path.join(CNF_DIR, "my.cnf.d")  # my.cnf.d 디렉토리
    MYSQL_CNF_DIR = os.path.join(CNF_DIR, "mysql")  # mysql 디렉토리
    DATA_DIR = "data"
    CONTAINER_NAME = "mariadb_backup"


def cleanup_docker() -> None:
    """Docker 컨테이너 및 볼륨 정리"""
    try:
        print("\nDocker 환경을 정리합니다...")
        subprocess.run(
            ["docker", "compose", "down", "-v"],
            check=True,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    except subprocess.CalledProcessError as e:
        raise MariaDBError(f"Docker 정리 중 오류 발생: {e.stderr}")
    except Exception as e:
        raise MariaDBError(f"예상치 못한 오류 발생: {str(e)}")


class ConfigManager:
    """설정 파일 관리 클래스"""

    def __init__(self):
        self._check_config_dirs()
        self.found_configs = self._find_configs()

    def _check_config_dirs(self) -> None:
        """기본 설정 디렉토리 존재 여부 확인"""
        if not os.path.exists(Constants.CNF_DIR):
            raise MariaDBError(f"{Constants.CNF_DIR} 디렉토리가 없습니다.")

    def _find_configs(self) -> dict:
        """존재하는 설정 파일/디렉토리 확인"""
        configs = {
            "my.cnf": os.path.exists(Constants.MY_CNF),
            "my.cnf.d": os.path.exists(Constants.MYCNF_D_DIR),
            "mysql": os.path.exists(Constants.MYSQL_CNF_DIR),
        }

        if not any(configs.values()):
            raise MariaDBError(
                f"{Constants.CNF_DIR} 디렉토리 안에 설정 파일/디렉토리가 없습니다.\n"
                f"원본 서버의 설정 파일들을 복사해주세요:\n"
                f"- my.cnf 파일 -> {Constants.MY_CNF}\n"
                f"- my.cnf.d 디렉토리 -> {Constants.MYCNF_D_DIR}\n"
                f"- mysql 디렉토리 -> {Constants.MYSQL_CNF_DIR}"
            )

        return configs

    def modify_socket_path(self, version: str) -> bool:
        """버전에 따른 소켓 경로 수정"""
        try:
            if not os.path.exists(Constants.MY_CNF):
                print(
                    f"\n{Constants.MY_CNF} 파일이 없습니다. 소켓 경로 수정을 건너뜁니다."
                )
                return True

            content = self._read_config()
            version_num = float(version.split(".")[1])
            socket_path = (
                Constants.OLD_SOCKET_PATH
                if version_num <= 1
                else Constants.DEFAULT_SOCKET_PATH
            )

            modified_content = re.sub(
                r"(socket\s*=\s*)/[^\n]*", r"\1" + socket_path, content
            )
            self._write_config(modified_content)
            print(
                f"\nMariaDB {version} 버전에 맞게 소켓 경로를 {socket_path}로 수정했습니다."
            )
            return True

        except Exception as e:
            raise MariaDBError(f"설정 파일 수정 중 오류 발생: {str(e)}")

    def _read_config(self) -> str:
        """설정 파일 읽기"""
        try:
            with open(Constants.MY_CNF, "r") as f:
                return f.read()
        except Exception as e:
            raise MariaDBError(f"설정 파일 읽기 실패: {str(e)}")

    def _write_config(self, content: str) -> None:
        """설정 파일 쓰기"""
        try:
            with open(Constants.MY_CNF, "w") as f:
                f.write(content)
        except Exception as e:
            raise MariaDBError(f"설정 파일 쓰기 실패: {str(e)}")


class DockerComposeManager:

    def __init__(self, version: str):
        self.version = version
        self.container_name = Constants.CONTAINER_NAME
        self.current_dir = os.path.abspath(os.getcwd())

    def create_config(self) -> bool:
        """Docker Compose 설정 파일 생성"""
        try:
            config = self._generate_config()
            print(
                f"\nDocker Compose 설정 파일 생성 중... (MariaDB 버전: {self.version})"
            )
            with open("docker-compose.yml", "w") as f:
                f.write(config)
            return True
        except Exception as e:
            raise MariaDBError(f"docker-compose.yml 생성 중 오류: {str(e)}")

    def _generate_config(self) -> str:
        """Docker Compose 설정 생성"""
        # 존재하는 설정 파일/디렉토리에 대한 볼륨 마운트 설정 생성
        volumes = [f"      - {self.current_dir}/{Constants.DATA_DIR}:/var/lib/mysql"]

        if self.config_manager.found_configs["my.cnf"]:
            volumes.append(
                f"      - {self.current_dir}/{Constants.MY_CNF}:/etc/my.cnf:ro"
            )

        if self.config_manager.found_configs["my.cnf.d"]:
            volumes.append(
                f"      - {self.current_dir}/{Constants.MYCNF_D_DIR}:/etc/my.cnf.d:ro"
            )

        if self.config_manager.found_configs["mysql"]:
            volumes.append(
                f"      - {self.current_dir}/{Constants.MYSQL_CNF_DIR}:/etc/mysql:ro"
            )

        volumes_str = "\n".join(volumes)

        return f"""services:
  {self.container_name}:
    image: mariadb:{self.version}
    container_name: {self.container_name}
    volumes:
{volumes_str}
    ports:
      - "3306:3306"
"""


class VersionManager:
    """MariaDB 버전 관리 클래스"""

    MARIADB_VERSIONS = {
        "10.0": [
            "10.0.15",
            "10.0.16",
            "10.0.17",
            "10.0.19",
            "10.0.20",
            "10.0.21",
            "10.0.22",
            "10.0.23",
            "10.0.24",
            "10.0.25",
            "10.0.26",
            "10.0.27",
            "10.0.28",
            "10.0.29",
            "10.0.30",
            "10.0.31",
            "10.0.32",
            "10.0.33",
            "10.0.34",
            "10.0.35",
            "10.0.36",
            "10.0.37",
            "10.0.38",
        ],
        "10.1": [
            "10.1.1",
            "10.1.2",
            "10.1.3",
            "10.1.10",
            "10.1.11",
            "10.1.12",
            "10.1.13",
            "10.1.14",
            "10.1.15",
            "10.1.16",
            "10.1.17",
            "10.1.18",
            "10.1.19",
            "10.1.20",
            "10.1.21",
            "10.1.22",
            "10.1.23",
            "10.1.24",
            "10.1.25",
            "10.1.26",
            "10.1.28",
            "10.1.29",
            "10.1.30",
            "10.1.31",
            "10.1.32",
            "10.1.33",
            "10.1.34",
            "10.1.35",
            "10.1.36",
            "10.1.37",
            "10.1.38",
            "10.1.39",
            "10.1.40",
            "10.1.41",
            "10.1.43",
            "10.1.44",
            "10.1.45",
            "10.1.46",
            "10.1.47",
            "10.1.48",
        ],
        "10.2": [
            "10.2.5",
            "10.2.6",
            "10.2.7",
            "10.2.8",
            "10.2.9",
            "10.2.10",
            "10.2.11",
            "10.2.12",
            "10.2.13",
            "10.2.14",
            "10.2.15",
            "10.2.16",
            "10.2.17",
            "10.2.18",
            "10.2.19",
            "10.2.20",
            "10.2.21",
            "10.2.22",
            "10.2.23",
            "10.2.24",
            "10.2.25",
            "10.2.26",
            "10.2.27",
            "10.2.29",
            "10.2.30",
            "10.2.31",
            "10.2.32",
            "10.2.33",
            "10.2.34",
            "10.2.35",
            "10.2.36",
            "10.2.37",
            "10.2.38",
            "10.2.39",
            "10.2.40",
            "10.2.41",
            "10.2.43",
            "10.2.44",
        ],
        "10.3": [
            "10.3.0",
            "10.3.1",
            "10.3.2",
            "10.3.3",
            "10.3.4",
            "10.3.5",
            "10.3.6",
            "10.3.7",
            "10.3.8",
            "10.3.9",
            "10.3.10",
            "10.3.11",
            "10.3.12",
            "10.3.13",
            "10.3.14",
            "10.3.15",
            "10.3.16",
            "10.3.17",
            "10.3.18",
            "10.3.20",
            "10.3.21",
            "10.3.22",
            "10.3.23",
            "10.3.24",
            "10.3.25",
            "10.3.26",
            "10.3.27",
            "10.3.28",
            "10.3.29",
            "10.3.30",
            "10.3.31",
            "10.3.32",
            "10.3.34",
            "10.3.35",
            "10.3.36",
            "10.3.37",
            "10.3.38",
            "10.3.39",
        ],
        "10.4": [
            "10.4.0",
            "10.4.1",
            "10.4.2",
            "10.4.3",
            "10.4.4",
            "10.4.5",
            "10.4.6",
            "10.4.7",
            "10.4.8",
            "10.4.10",
            "10.4.11",
            "10.4.12",
            "10.4.13",
            "10.4.14",
            "10.4.15",
            "10.4.16",
            "10.4.17",
            "10.4.18",
            "10.4.19",
            "10.4.20",
            "10.4.21",
            "10.4.22",
            "10.4.24",
            "10.4.25",
            "10.4.26",
            "10.4.27",
            "10.4.28",
            "10.4.29",
            "10.4.30",
            "10.4.31",
            "10.4.32",
            "10.4.33",
            "10.4.34",
        ],
        "10.5": [
            "10.5.1",
            "10.5.2",
            "10.5.3",
            "10.5.4",
            "10.5.5",
            "10.5.6",
            "10.5.7",
            "10.5.8",
            "10.5.9",
            "10.5.10",
            "10.5.11",
            "10.5.12",
            "10.5.13",
            "10.5.15",
            "10.5.16",
            "10.5.17",
            "10.5.18",
            "10.5.19",
            "10.5.20",
            "10.5.21",
            "10.5.22",
            "10.5.23",
            "10.5.24",
            "10.5.25",
            "10.5.26",
            "10.5.27",
            "10.5.28",
        ],
        "10.6": [
            "10.6.0",
            "10.6.1",
            "10.6.2",
            "10.6.3",
            "10.6.4",
            "10.6.5",
            "10.6.7",
            "10.6.8",
            "10.6.9",
            "10.6.10",
            "10.6.11",
            "10.6.12",
            "10.6.13",
            "10.6.14",
            "10.6.15",
            "10.6.16",
            "10.6.17",
            "10.6.18",
            "10.6.19",
            "10.6.20",
            "10.6.21",
        ],
        "10.7": ["10.7.1", "10.7.3", "10.7.4", "10.7.5", "10.7.6", "10.7.7", "10.7.8"],
        "10.8": ["10.8.2", "10.8.3", "10.8.4", "10.8.5", "10.8.6", "10.8.7", "10.8.8"],
        "10.9": ["10.9.2", "10.9.3", "10.9.4", "10.9.5", "10.9.6", "10.9.7", "10.9.8"],
        "10.10": ["10.10.2", "10.10.3", "10.10.4", "10.10.5", "10.10.6", "10.10.7"],
        "10.11": [
            "10.11.2",
            "10.11.3",
            "10.11.4",
            "10.11.5",
            "10.11.6",
            "10.11.7",
            "10.11.8",
            "10.11.9",
            "10.11.10",
            "10.11.11",
        ],
        "11.0": ["11.0.2", "11.0.3", "11.0.4", "11.0.5", "11.0.6"],
        "11.1": ["11.1.2", "11.1.3", "11.1.4", "11.1.5", "11.1.6"],
        "11.2": ["11.2.2", "11.2.3", "11.2.4", "11.2.5", "11.2.6"],
        "11.3": ["11.3.2"],
        "11.4": ["11.4.2", "11.4.3", "11.4.4", "11.4.5"],
        "11.5": ["11.5.2"],
        "11.6": ["11.6.2"],
    }

    @classmethod
    def display_versions(cls) -> List[str]:
        """버전 목록 표시"""
        try:
            all_versions = []
            version_count = 1

            print("\nMySQL 버전 선택:")
            print("=" * 120)

            for major_version, versions in cls.MARIADB_VERSIONS.items():
                if versions:  # 버전이 있는 그룹만 표시
                    print(f"\n{major_version}.x:")
                    print("-" * 120)

                    current_line = []
                    for version in versions:
                        all_versions.append(version)
                        current_line.append(f"{version_count:3d}. {version:12}")
                        version_count += 1

                        if len(current_line) == 5:  # 한 줄에 5개씩 표시
                            print("".join(current_line))
                            current_line = []

                    if current_line:  # 마지막 줄 출력
                        print("".join(current_line))

            return all_versions

        except Exception as e:
            raise MariaDBError(f"버전 표시 중 오류 발생: {str(e)}")

    @classmethod
    def select_version(cls, versions: List[str]) -> Optional[str]:
        """버전 선택"""
        attempts = 0
        while attempts < Constants.MAX_ATTEMPTS:
            try:
                choice = input("\n버전 번호를 입력하세요 (0: 종료): ").strip()
                if not choice:
                    print("입력값이 없습니다.")
                    continue

                if choice == "0":
                    print("프로그램을 종료합니다.")
                    exit(0)

                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(versions):
                    return versions[choice_idx]

                print(f"1-{len(versions)} 사이의 숫자를 입력하세요.")

            except ValueError:
                print("올바른 숫자를 입력하세요.")
            except Exception as e:
                print(f"\n오류 발생: {str(e)}")

            attempts += 1

        print("최대 시도 횟수를 초과했습니다.")
        return None


def signal_handler(sig, frame):
    """시그널 핸들러"""
    print("\n\n프로그램을 안전하게 종료합니다.")
    cleanup_docker()
    sys.exit(0)


def main():
    """메인 함수"""
    signal.signal(signal.SIGINT, signal_handler)

    try:
        # 데이터 디렉토리 생성
        os.makedirs(Constants.DATA_DIR, exist_ok=True)

        # 설정 관리자 초기화
        config_manager = ConfigManager()

        # MariaDB 버전 선택
        versions = VersionManager.display_versions()
        target_version = VersionManager.select_version(versions)

        # 설정 파일 소켓 경로 수정
        config_manager.modify_socket_path(target_version)

        # Docker Compose 설정 생성 및 컨테이너 실행
        docker_manager = DockerComposeManager(target_version)
        docker_manager.config_manager = config_manager

        docker_manager.create_config()

        # 컨테이너 실행
        print("\nDocker 컨테이너를 시작합니다...")
        subprocess.run(["docker", "compose", "up", "-d"], check=True)

        print(f"\n컨테이너가 실행되었습니다.")
        print(
            f"\n접속 방법: docker container exec -it {docker_manager.container_name} bash"
        )
        print("종료 시: docker compose down -v")

    except MariaDBError as e:
        print(f"\n오류 발생: {str(e)}")
        cleanup_docker()
        sys.exit(1)
    except Exception as e:
        print(f"\n예상치 못한 오류 발생: {str(e)}")
        cleanup_docker()
        sys.exit(1)


if __name__ == "__main__":
    main()
