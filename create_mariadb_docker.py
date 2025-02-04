#!/usr/bin/env python3
import os
import signal
import time
import re
import subprocess
from datetime import datetime
from typing import List, Dict, Tuple, Optional

# 상수 정의
CONSTANTS = {
    "MAX_ATTEMPTS": 3,
    "CNF_PATH": "cnf/50-server.cnf",  # 원본 서버의 설정 파일 경로
    "DATA_DIR": "data",
    "SQL_DIR": "sql",
    "MARIADB_USER": "root",
    "BACKUP_DIR": "/var/lib/mysql",
}


class MariaDBConfigError(Exception):
    """MariaDB 설정 관련 커스텀 예외"""

    pass


class ConfigManager:
    """설정 파일 관리 클래스"""

    def __init__(self, cnf_path: str):
        self.cnf_path = cnf_path

    def check_exists(self) -> bool:
        """설정 파일 존재 여부 확인"""
        if not os.path.exists(self.cnf_path):
            raise MariaDBConfigError(
                f"복원에 필요한 MariaDB 설정 파일({self.cnf_path})이 없습니다.\n"
                f"원본 서버의 설정 파일을 {self.cnf_path} 경로에 복사해주세요."
            )
        return True


class DockerComposeManager:
    """Docker Compose 설정 관리 클래스"""

    def __init__(self, version: str):
        self.version = version
        self.container_name = f"mariadb_{version.replace('.', '_')}"
        self.current_dir = os.path.abspath(os.getcwd())

    def create_config(self) -> bool:
        """Docker Compose 설정 파일 생성"""
        try:
            config = self._generate_config()
            with open("docker-compose.yml", "w") as f:
                f.write(config)
            return True
        except Exception as e:
            raise MariaDBConfigError(f"docker-compose.yml 생성 중 오류 발생: {str(e)}")

    def _generate_config(self) -> str:
        """Docker Compose 설정 생성"""
        return f"""version: '3'
services:
  {self.container_name}:
    image: mariadb:{self.version}
    container_name: {self.container_name}
    volumes:
      - {self.current_dir}/{CONSTANTS['DATA_DIR']}:/var/lib/mysql
      - {self.current_dir}/{CONSTANTS['CNF_PATH']}:/etc/mysql/mariadb.conf.d/50-server.cnf:ro
    ports:
      - "3306:3306"
    environment:
      - MYSQL_ALLOW_EMPTY_PASSWORD=yes
"""


class BackupManager:
    """백업 관리 클래스"""

    def __init__(self, container_name: str):
        self.container_name = container_name
        self.date_str = datetime.now().strftime("%Y%m%d")

    def get_backup_target(self) -> Optional[Tuple[str, str, str]]:
        """백업 대상과 비밀번호 입력 받기"""
        try:
            print("\n백업 대상을 입력하세요.")
            print("형식: database 또는 database.table")
            target = input("입력: ").strip()

            if not target:
                print("입력값이 없습니다.")
                return None

            password = input("\nMariaDB root 비밀번호를 입력하세요: ").strip()

            if "." in target:
                db, table = target.split(".")
                return (db, table, password)
            return (target, None, password)

        except Exception as e:
            raise MariaDBConfigError(f"백업 대상 입력 중 오류 발생: {str(e)}")

    def execute_backup(
        self, db: str, password: str, table: Optional[str] = None
    ) -> bool:
        """백업 실행"""
        try:
            sql_dir = os.path.join(os.getcwd(), CONSTANTS["SQL_DIR"])
            os.makedirs(sql_dir, exist_ok=True)

            print("\nDocker 컨테이너 시작 중...")
            subprocess.run(["docker", "compose", "up", "-d"], check=True)

            print("MariaDB 서버 준비 대기 중...")
            time.sleep(10)

            backup_target = f"{db}.{table}" if table else db
            backup_file = f"{self.date_str}_{backup_target.replace('.', '_')}.sql"
            container_backup_path = f"{CONSTANTS['BACKUP_DIR']}/{backup_file}"
            local_backup_path = os.path.join(sql_dir, backup_file)

            print(f"\n{backup_target} 백업 중...")
            dump_cmd = f"mysqldump -u {CONSTANTS['MARIADB_USER']} -p'{password}' {backup_target} > {container_backup_path}"
            exec_cmd = [
                "docker",
                "container",
                "exec",
                self.container_name,
                "bash",
                "-c",
                dump_cmd,
            ]

            process = subprocess.Popen(
                exec_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            _, stderr = process.communicate()

            if process.returncode != 0:
                if "Access denied" in stderr:
                    raise MariaDBConfigError("MariaDB 비밀번호가 올바르지 않습니다.")
                raise MariaDBConfigError(f"mysqldump 실행 중 오류: {stderr}")

            cp_cmd = f"docker cp {self.container_name}:{container_backup_path} {local_backup_path}"
            subprocess.run(cp_cmd, shell=True, check=True)

            print("\n컨테이너 정지 중...")
            subprocess.run(["docker", "compose", "stop"], check=True)

            print(f"\n백업 완료: {CONSTANTS['SQL_DIR']}/{backup_file}")
            return True

        except subprocess.CalledProcessError as e:
            raise MariaDBConfigError(f"백업 실행 중 오류 발생: {str(e)}")
        except Exception as e:
            raise MariaDBConfigError(f"예상치 못한 오류 발생: {str(e)}")


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
        ],
        "11.0": ["11.0.2", "11.0.3", "11.0.4", "11.0.5", "11.0.6"],
        "11.1": ["11.1.2", "11.1.3", "11.1.4", "11.1.5", "11.1.6"],
        "11.2": ["11.2.2", "11.2.3", "11.2.4", "11.2.5", "11.2.6"],
        "11.3": ["11.3.2"],
        "11.4": ["11.4.2", "11.4.3", "11.4.4"],
        "11.5": ["11.5.2"],
        "11.6": ["11.6.2"],
    }

    @staticmethod
    def display_versions() -> List[str]:
        """버전 목록 표시"""
        try:
            all_versions = []
            version_count = 1

            print("\nMariaDB 버전 선택:")
            print("=" * 120)

            for major_version, versions in VersionManager.MARIADB_VERSIONS.items():
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
            raise MariaDBConfigError(f"버전 표시 중 오류 발생: {str(e)}")

    @staticmethod
    def select_version(versions: List[str]) -> Optional[str]:
        """버전 선택"""
        attempts = 0
        while attempts < CONSTANTS["MAX_ATTEMPTS"]:
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
    exit(0)


def main():
    """메인 함수"""
    signal.signal(signal.SIGINT, signal_handler)

    try:
        # 데이터 디렉토리 생성
        os.makedirs(CONSTANTS["DATA_DIR"], exist_ok=True)

        # 설정 관리자 초기화 및 설정 파일 체크
        config_manager = ConfigManager(CONSTANTS["CNF_PATH"])
        config_manager.check_exists()

        # 버전 선택
        versions = VersionManager.display_versions()
        version = VersionManager.select_version(versions)

        if version:
            # Docker Compose 설정 생성
            docker_manager = DockerComposeManager(version)

            if docker_manager.create_config():
                # 백업 관리자 초기화 및 백업 실행
                backup_manager = BackupManager(docker_manager.container_name)
                backup_info = backup_manager.get_backup_target()

                if backup_info:
                    db, table, password = backup_info
                    backup_manager.execute_backup(db, password, table)

                print("\n모든 작업이 완료되었습니다.")

    except MariaDBConfigError as e:
        print(f"\n설정 오류: {str(e)}")
        exit(1)
    except Exception as e:
        print(f"\n예상치 못한 오류: {str(e)}")
        exit(1)


if __name__ == "__main__":
    main()
