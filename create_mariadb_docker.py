#!/usr/bin/env python3
import os
import signal
import subprocess
import time
import re
from datetime import datetime
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
    MARIADB_USER = "root"
    MY_CNF = os.path.join(CNF_DIR, "my.cnf")  # my.cnf 파일
    MYCNF_D_DIR = os.path.join(CNF_DIR, "my.cnf.d")  # my.cnf.d 디렉토리
    MYSQL_CNF_DIR = os.path.join(CNF_DIR, "mysql")  # mysql 디렉토리
    SQL_DIR = "sql"
    DATA_DIR = "data"
    BACKUP_DIR = "/var/lib/mysql"
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


class DockerContainerBase:
    """Docker 컨테이너 기본 기능 클래스"""

    def __init__(self, container_name: str):
        self.container_name = container_name

    def start_container(self) -> bool:
        """컨테이너 시작"""
        try:
            print("\nDocker 컨테이너 시작 중...")
            subprocess.run(["docker", "compose", "up", "-d"], check=True)
            return True
        except Exception as e:
            # 시작 실패 시에만 cleanup 실행
            cleanup_docker()
            raise MariaDBError(f"컨테이너 시작 중 오류 발생: {str(e)}")

    def stop_container(self) -> bool:
        """컨테이너 중지 및 정리"""
        try:
            print("\n컨테이너 정지 및 정리 중...")
            cleanup_docker()
            return True
        except Exception as e:
            raise MariaDBError(f"컨테이너 정지 중 오류 발생: {str(e)}")

    def is_container_running(self) -> bool:
        """컨테이너 실행 상태 확인"""
        try:
            result = subprocess.run(
                [
                    "docker",
                    "container",
                    "inspect",
                    "-f",
                    "{{.State.Running}}",
                    self.container_name,
                ],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0 and "true" in result.stdout.lower()
        except:
            return False


class MariaDBCommandBuilder:
    """MySQL 명령어 생성 유틸리티 클래스"""

    @staticmethod
    def get_mariadb_command(user: str, password: Optional[str] = None) -> str:
        pwd_option = f"-p'{password}'" if password else ""
        return f"mysql -u {user} {pwd_option}"

    @staticmethod
    def get_mariadbdump_command(
        user: str, password: str, target: str, output_path: str
    ) -> str:
        pwd_option = f"-p'{password}'" if password else ""
        return f"mysqldump -u {user} {pwd_option} {target} > {output_path}"

    @staticmethod
    def get_mariadb_ping_command(user: str, password: Optional[str] = None) -> str:
        pwd_option = f"-p'{password}'" if password else ""
        return f"mysqladmin -u {user} {pwd_option} ping"


class ConfigManager:
    """설정 파일 관리 클래스"""

    def __init__(self):
        self.found_configs = self._find_configs()

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

    def check_config_dirs(self) -> None:
        """기본 설정 디렉토리 존재 여부 확인"""
        if not os.path.exists(Constants.CNF_DIR):
            raise MariaDBError(f"{Constants.CNF_DIR} 디렉토리가 없습니다.")

    def modify_socket_path(self, version: str) -> bool:
        """버전에 따른 소켓 경로 수정"""
        try:
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
        with open(Constants.MY_CNF, "r") as f:
            return f.read()

    def _write_config(self, content: str) -> None:
        """설정 파일 쓰기"""
        with open(Constants.MY_CNF, "w") as f:
            f.write(content)


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


class BackupManager(DockerContainerBase):
    """백업 관리 클래스"""

    def __init__(self, container_name: str):
        super().__init__(container_name)
        self.date_str = datetime.now().strftime("%Y%m%d")
        self.init_timeout = 300  # 5분
        self.operation_timeout = 7200  # 2시간
        self.mariadb_cmd = MariaDBCommandBuilder()

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
            raise MariaDBError(f"백업 대상 입력 중 오류 발생: {str(e)}")

    def wait_for_mariadb_ready(
        self, max_attempts: Optional[int] = None, password: Optional[str] = None
    ) -> bool:
        """MariaDB 서버가 준비될 때까지 대기"""
        if max_attempts is None:
            max_attempts = self.init_timeout

        print("\nMariaDB 서버 준비 대기 중...")
        print(f"최대 대기 시간: {max_attempts}초")
        attempt = 0

        while attempt < max_attempts:
            try:
                if attempt % 5 == 0:
                    check_cmd = self.mariadb_cmd.get_mariadb_ping_command(
                        Constants.MARIADB_USER, password
                    )
                    exec_cmd = [
                        "docker",
                        "container",
                        "exec",
                        self.container_name,
                        "bash",
                        "-c",
                        check_cmd,
                    ]

                    result = subprocess.run(
                        exec_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                    )

                    if result.returncode == 0 and "mysqld is alive" in result.stdout:
                        print("\nMariaDB 서버 준비 완료!")
                        return True

                    if "Access denied" in result.stderr and not password:
                        raise MariaDBError("MariaDB 접속을 위한 비밀번호가 필요합니다.")

                if attempt > 0 and attempt % 10 == 0:
                    print(f"\n{attempt}초 경과... (서버 초기화 중)")
                else:
                    print(".", end="", flush=True)

            except subprocess.CalledProcessError:
                pass

            time.sleep(1)
            attempt += 1

        raise MariaDBError(f"MariaDB 서버 준비 시간 초과 ({max_attempts}초)")

    def execute_backup(
        self, db: str, password: str, table: Optional[str] = None
    ) -> bool:
        """백업 실행"""
        try:
            sql_dir = os.path.join(os.getcwd(), Constants.SQL_DIR)
            os.makedirs(sql_dir, exist_ok=True)

            if not self.is_container_running():
                self.start_container()

            self.wait_for_mariadb_ready(password=password)

            backup_target = f"{db}.{table}" if table else db
            backup_file = f"{self.date_str}_{backup_target.replace('.', '_')}.sql"
            container_backup_path = f"{Constants.BACKUP_DIR}/{backup_file}"
            local_backup_path = os.path.join(sql_dir, backup_file)

            print(f"\n{backup_target} 백업 중...")
            self._perform_backup(backup_target, password, container_backup_path)
            self._copy_backup_to_local(container_backup_path, local_backup_path)

            self.stop_container()

            print(f"\n백업 완료: {Constants.SQL_DIR}/{backup_file}")
            return True

        except Exception as e:
            self.stop_container()  # 오류 발생 시에도 정리
            raise MariaDBError(f"백업 실행 중 오류 발생: {str(e)}")

    def _perform_backup(
        self, backup_target: str, password: str, backup_path: str
    ) -> None:
        """백업 실행"""
        dump_cmd = self.mariadb_cmd.get_mariadbdump_command(
            Constants.MARIADB_USER, password, backup_target, backup_path
        )
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
                raise MariaDBError("MySQL 비밀번호가 올바르지 않습니다.")
            raise MariaDBError(f"mysqldump 실행 중 오류: {stderr}")

    def _copy_backup_to_local(self, container_path: str, local_path: str) -> None:
        """백업 파일을 로컬로 복사"""
        subprocess.run(
            f"docker cp {self.container_name}:{container_path} {local_path}",
            shell=True,
            check=True,
        )


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
    exit(0)


def main():
    """메인 함수"""
    signal.signal(signal.SIGINT, signal_handler)

    try:
        # 데이터 디렉토리 생성
        os.makedirs(Constants.DATA_DIR, exist_ok=True)

        # 설정 관리자 초기화
        config_manager = ConfigManager()
        config_manager.check_config_dirs()

        # MariaDB 버전 선택
        print("\n도커 컨테이너에서 실행할 MariaDB 버전을 선택하세요:")
        versions = VersionManager.display_versions()
        target_version = VersionManager.select_version(versions)

        if target_version:
            # 버전별 소켓 경로 수정
            config_manager.modify_socket_path(target_version)

            # Docker Compose 설정 생성
            docker_manager = DockerComposeManager(target_version)
            docker_manager.config_manager = config_manager
            docker_manager.create_config()

            # ToDo
            # 백업 관리자 초기화
            backup_manager = BackupManager(docker_manager.container_name)

            print("\n작업을 선택하세요:")
            print("1. 컨테이너만 실행")
            print("2. 백업 진행")

            choice = input("선택 (1 또는 2): ").strip()

            if choice == "1":
                backup_manager.start_container()
                print("\n컨테이너 실행이 완료되었씁니다.")
                print(
                    f"\n접속 방법: docker container exec -it {docker_manager.container_name}"
                )
                print("필수! 종료: docker compose down -v")
            elif choice == "2":
                # 버전이 같은 경우 바로 백업 진행
                backup_info = backup_manager.get_backup_target()
                if backup_info:
                    db, table, password = backup_info
                    backup_manager.execute_backup(db, password, table)
                else:
                    print("\n백업 대상 정보 입력이 취소되었습니다.")
                    exit(1)
            else:
                print("\n잘못된 선택입니다.")
                exit(1)

    except MariaDBError as e:
        print(f"\n오류 발생: {str(e)}")
        cleanup_docker()
        exit(1)
    except Exception as e:
        print(f"\n예상치 못한 오류 발생: {str(e)}")
        cleanup_docker()
        exit(1)


if __name__ == "__main__":
    main()
