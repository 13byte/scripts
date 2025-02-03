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
    "DEFAULT_SOCKET_PATH": "/var/lib/mysql/mysql.sock",
    "V56_SOCKET_PATH": "/var/run/mysqld/mysqld.sock",
    "CNF_PATH": "cnf/my.cnf",
    "DATA_DIR": "data",
    "SQL_DIR": "sql",
    "MYSQL_USER": "root",
    "BACKUP_DIR": "/var/lib/mysql",
}


class MySQLConfigError(Exception):
    """MySQL 설정 관련 커스텀 예외"""

    pass


class ConfigManager:
    """설정 파일 관리 클래스"""

    def __init__(self, cnf_path: str):
        self.cnf_path = cnf_path

    def check_exists(self) -> bool:
        """설정 파일 존재 여부 확인"""
        if not os.path.exists(self.cnf_path):
            raise MySQLConfigError(f"{self.cnf_path}가 없습니다.")
        return True

    def modify_socket_path(self, version: str) -> bool:
        """버전에 따른 소켓 경로 수정"""
        try:
            content = self._read_config()
            socket_path = (
                CONSTANTS["V56_SOCKET_PATH"]
                if version.startswith("5.6")
                else CONSTANTS["DEFAULT_SOCKET_PATH"]
            )
            modified_content = re.sub(
                r"(socket\s*=\s*)/[^\n]*", r"\1" + socket_path, content
            )
            self._write_config(modified_content)
            return True
        except Exception as e:
            raise MySQLConfigError(f"my.cnf 수정 중 오류 발생: {str(e)}")

    def _read_config(self) -> str:
        """설정 파일 읽기"""
        with open(self.cnf_path, "r") as f:
            return f.read()

    def _write_config(self, content: str) -> None:
        """설정 파일 쓰기"""
        with open(self.cnf_path, "w") as f:
            f.write(content)


class DockerComposeManager:
    """Docker Compose 설정 관리 클래스"""

    def __init__(self, version: str):
        self.version = version
        self.container_name = f"mysql_{version.replace('.', '_')}"
        self.current_dir = os.path.abspath(os.getcwd())

    def create_config(self) -> bool:
        """Docker Compose 설정 파일 생성"""
        try:
            config = self._generate_config()
            with open("docker-compose.yml", "w") as f:
                f.write(config)
            return True
        except Exception as e:
            raise MySQLConfigError(f"docker-compose.yml 생성 중 오류 발생: {str(e)}")

    def _generate_config(self) -> str:
        """Docker Compose 설정 생성"""
        return f"""version: '3'
services:
  {self.container_name}:
    image: mysql:{self.version}
    container_name: {self.container_name}
    volumes:
      - {self.current_dir}/{CONSTANTS['DATA_DIR']}:/var/lib/mysql
      - {self.current_dir}/{CONSTANTS['CNF_PATH']}:/etc/my.cnf
    ports:
      - "3306:3306"
    command: --defaults-file=/etc/my.cnf
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

            password = input("\nMySQL root 비밀번호를 입력하세요: ").strip()

            if "." in target:
                db, table = target.split(".")
                return (db, table, password)
            return (target, None, password)

        except Exception as e:
            raise MySQLConfigError(f"백업 대상 입력 중 오류 발생: {str(e)}")

    def execute_backup(
        self, db: str, password: str, table: Optional[str] = None
    ) -> bool:
        """백업 실행"""
        try:
            # SQL 디렉토리 생성
            sql_dir = os.path.join(os.getcwd(), CONSTANTS["SQL_DIR"])
            os.makedirs(sql_dir, exist_ok=True)

            print("\nDocker 컨테이너 시작 중...")
            subprocess.run(["docker", "compose", "up", "-d"], check=True)

            print("MySQL 서버 준비 대기 중...")
            time.sleep(10)

            # 백업 파일 경로 설정
            backup_target = f"{db}.{table}" if table else db
            backup_file = f"{self.date_str}_{backup_target.replace('.', '_')}.sql"
            container_backup_path = f"{CONSTANTS['BACKUP_DIR']}/{backup_file}"
            local_backup_path = os.path.join(sql_dir, backup_file)

            print(f"\n{backup_target} 백업 중...")
            dump_cmd = f"mysqldump -u {CONSTANTS['MYSQL_USER']} -p'{password}' {backup_target} > {container_backup_path}"
            exec_cmd = [
                "docker",
                "container",
                "exec",
                self.container_name,
                "bash",
                "-c",
                dump_cmd,
            ]

            # mysqldump 실행 (capture_output 대신 PIPE 사용)
            process = subprocess.Popen(
                exec_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            _, stderr = process.communicate()

            if process.returncode != 0:
                if "Access denied" in stderr:
                    raise MySQLConfigError("MySQL 비밀번호가 올바르지 않습니다.")
                raise MySQLConfigError(f"mysqldump 실행 중 오류: {stderr}")

            # 컨테이너에서 로컬로 파일 복사
            cp_cmd = f"docker cp {self.container_name}:{container_backup_path} {local_backup_path}"
            subprocess.run(cp_cmd, shell=True, check=True)

            print("\n컨테이너 정지 중...")
            subprocess.run(["docker", "compose", "stop"], check=True)

            print(f"\n백업 완료: {CONSTANTS['SQL_DIR']}/{backup_file}")
            return True

        except subprocess.CalledProcessError as e:
            raise MySQLConfigError(f"백업 실행 중 오류 발생: {str(e)}")
        except Exception as e:
            raise MySQLConfigError(f"예상치 못한 오류 발생: {str(e)}")


class VersionManager:
    """MySQL 버전 관리 클래스"""

    @staticmethod
    def generate_versions() -> Tuple[List[str], ...]:
        """지원되는 MySQL 버전 목록 생성"""
        v5 = [
            "5.6.17",
            "5.6.20",
            "5.6.21",
            "5.6.22",
            "5.6.23",
            "5.6.24",
            "5.6.25",
            "5.6.26",
            "5.6.27",
            "5.6.28",
            "5.6.29",
            "5.6.30",
            "5.6.31",
            "5.6.32",
            "5.6.33",
            "5.6.34",
            "5.6.35",
            "5.6.36",
            "5.6.37",
            "5.6.38",
            "5.6.39",
            "5.6.40",
            "5.6.41",
            "5.6.42",
            "5.6.43",
            "5.6.44",
            "5.6.45",
            "5.6.46",
            "5.6.47",
            "5.6.48",
            "5.6.49",
            "5.6.50",
            "5.6.51",
        ]

        v7 = [
            "5.7.4",
            "5.7.5",
            "5.7.6",
            "5.7.7",
            "5.7.8",
            "5.7.9",
            "5.7.10",
            "5.7.11",
            "5.7.12",
            "5.7.13",
            "5.7.14",
            "5.7.15",
            "5.7.16",
            "5.7.17",
            "5.7.18",
            "5.7.19",
            "5.7.20",
            "5.7.21",
            "5.7.22",
            "5.7.23",
            "5.7.24",
            "5.7.25",
            "5.7.26",
            "5.7.27",
            "5.7.28",
            "5.7.29",
            "5.7.30",
            "5.7.31",
            "5.7.32",
            "5.7.33",
            "5.7.34",
            "5.7.35",
            "5.7.36",
            "5.7.37",
            "5.7.38",
            "5.7.39",
            "5.7.40",
            "5.7.41",
            "5.7.42",
            "5.7.43",
            "5.7.44",
        ]

        v8 = [
            "8.0.0",
            "8.0.1",
            "8.0.2",
            "8.0.3",
            "8.0.4",
            "8.0.11",
            "8.0.12",
            "8.0.13",
            "8.0.14",
            "8.0.15",
            "8.0.16",
            "8.0.17",
            "8.0.18",
            "8.0.19",
            "8.0.20",
            "8.0.21",
            "8.0.22",
            "8.0.23",
            "8.0.24",
            "8.0.25",
            "8.0.26",
            "8.0.27",
            "8.0.28",
            "8.0.29",
            "8.0.30",
            "8.0.31",
            "8.0.32",
            "8.0.33",
            "8.0.34",
            "8.0.35",
            "8.0.36",
            "8.0.37",
            "8.0.38",
            "8.0.39",
            "8.0.40",
            "8.0.41",
        ]

        return (v5, v7, v8)

    @staticmethod
    def display_versions() -> List[str]:
        """버전 목록 표시"""
        try:
            v5, v7, v8 = VersionManager.generate_versions()
            max_len = max(len(v5), len(v7), len(v8))

            print("\nMySQL 버전 선택:")
            print("=" * 80)
            fmt = "{:<20}{:<20}{:<20}"
            print(fmt.format("5.6.x", "5.7.x", "8.0.x"))
            print("-" * 80)

            for i in range(0, max_len, 3):
                rows = []
                for j in range(3):
                    idx = i + j
                    v5_str = f"{idx+1}. {v5[idx]}" if idx < len(v5) else ""
                    v7_str = f"{len(v5)+idx+1}. {v7[idx]}" if idx < len(v7) else ""
                    v8_str = (
                        f"{len(v5)+len(v7)+idx+1}. {v8[idx]}" if idx < len(v8) else ""
                    )
                    rows.append(fmt.format(v5_str, v7_str, v8_str))
                print("\n".join(rows))

            return v5 + v7 + v8
        except Exception as e:
            raise MySQLConfigError(f"버전 표시 중 오류 발생: {str(e)}")

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
            # 설정 파일 수정
            config_manager.modify_socket_path(version)

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

    except MySQLConfigError as e:
        print(f"\n설정 오류: {str(e)}")
        exit(1)
    except Exception as e:
        print(f"\n예상치 못한 오류: {str(e)}")
        exit(1)


if __name__ == "__main__":
    main()
