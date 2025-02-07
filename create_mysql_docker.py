#!/usr/bin/env python3
import os
import signal
import time
import re
import subprocess
from datetime import datetime
from typing import List, Dict, Tuple, Optional


class Constants:
    """상수 정의 클래스"""

    MAX_ATTEMPTS = 3
    DEFAULT_SOCKET_PATH = "/var/lib/mysql/mysql.sock"
    V56_SOCKET_PATH = "/var/run/mysqld/mysqld.sock"
    CNF_PATH = "cnf/my.cnf"
    DATA_DIR = "data"
    SQL_DIR = "sql"
    MYSQL_USER = "root"
    BACKUP_DIR = "/var/lib/mysql"
    CONTAINER_NAME = "mysql_backup"


class MySQLConfigError(Exception):
    """MySQL 설정 관련 커스텀 예외"""

    pass


def cleanup_docker():
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
        print(f"Docker 정리 중 오류 발생: {e.stderr}")
    except Exception as e:
        print(f"예상치 못한 오류 발생: {str(e)}")


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
            raise MySQLConfigError(f"컨테이너 시작 중 오류 발생: {str(e)}")

    def stop_container(self) -> bool:
        """컨테이너 중지 및 정리"""
        try:
            print("\n컨테이너 정지 및 정리 중...")
            cleanup_docker()
            return True
        except Exception as e:
            raise MySQLConfigError(f"컨테이너 정지 중 오류 발생: {str(e)}")

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


class MySQLCommandBuilder:
    """MySQL 명령어 생성 유틸리티 클래스"""

    @staticmethod
    def get_mysql_command(user: str, password: Optional[str] = None) -> str:
        pwd_option = f"-p'{password}'" if password else ""
        return f"mysql -u {user} {pwd_option}"

    @staticmethod
    def get_mysqldump_command(
        user: str, password: str, target: str, output_path: str
    ) -> str:
        pwd_option = f"-p'{password}'" if password else ""
        return f"mysqldump --defaults-file=/etc/my.cnf -u {user} {pwd_option} {target} > {output_path}"

    @staticmethod
    def get_mysql_ping_command(user: str, password: Optional[str] = None) -> str:
        pwd_option = f"-p'{password}'" if password else ""
        return f"mysqladmin -u {user} {pwd_option} ping"


class ConfigManager:
    """설정 파일 관리 클래스"""

    def __init__(self, cnf_path: str):
        self.cnf_path = cnf_path

    def check_exists(self) -> bool:
        """설정 파일 존재 여부 확인"""
        if not os.path.exists(self.cnf_path):
            raise MySQLConfigError(
                f"복원에 필요한 MySQL 설정 파일({self.cnf_path})이 없습니다.\n"
                f"원본 서버의 설정 파일을 {self.cnf_path} 경로에 복사해주세요."
            )
        return True

    def modify_socket_path(self, version: str) -> bool:
        """버전에 따른 소켓 경로 수정"""
        try:
            content = self._read_config()
            # 버전 확인 로직
            version_parts = version.split(".")
            if len(version_parts) >= 2:
                version_num = float(f"{version_parts[0]}.{version_parts[1]}")
                sub_version = float(version_parts[2]) if len(version_parts) > 2 else 0

                need_old_socket = (
                    version.startswith("5.6")
                    or (version_num == 5.7 and sub_version <= 36)
                    or (version_num == 8.0 and sub_version <= 28)
                )

                socket_path = (
                    Constants.V56_SOCKET_PATH
                    if need_old_socket
                    else Constants.DEFAULT_SOCKET_PATH
                )

                modified_content = re.sub(
                    r"(socket\s*=\s*)/[^\n]*", r"\1" + socket_path, content
                )
                self._write_config(modified_content)
                print(
                    f"MySQL {version} 버전에 맞게 소켓 경로를 {socket_path}로 수정했습니다."
                )
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
        self.container_name = Constants.CONTAINER_NAME
        self.current_dir = os.path.abspath(os.getcwd())

    def create_config(self) -> bool:
        """Docker Compose 설정 파일 생성"""
        try:
            config = self._generate_config()
            print(f"\nDocker Compose 설정 파일 생성 중... (MySQL 버전: {self.version})")
            with open("docker-compose.yml", "w") as f:
                f.write(config)
            return True
        except Exception as e:
            raise MySQLConfigError(f"docker-compose.yml 생성 중 오류 발생: {str(e)}")

    def _generate_config(self) -> str:
        """Docker Compose 설정 생성"""
        version_parts = self.version.split(".")

        # 8.0.15 이하 버전에만 secure-file-priv 옵션 추가
        if (
            version_parts[0] == "8"
            and version_parts[1] == "0"
            and float(version_parts[2]) <= 28
        ):
            secure_option = " --secure-file-priv=''"
        else:
            secure_option = ""

        return f"""services:
        {self.container_name}:
            image: mysql:{self.version}
            container_name: {self.container_name}
            volumes:
            - {self.current_dir}/{Constants.DATA_DIR}:/var/lib/mysql
            - {self.current_dir}/{Constants.CNF_PATH}:/etc/my.cnf:ro
            ports:
            - "3306:3306"
            command: --defaults-file=/etc/my.cnf{secure_option}
    """


class BackupManager(DockerContainerBase):
    """백업 관리 클래스"""

    def __init__(self, container_name: str):
        super().__init__(container_name)
        self.date_str = datetime.now().strftime("%Y%m%d")
        self.init_timeout = 300  # 5분
        self.operation_timeout = 7200  # 2시간
        self.mysql_cmd = MySQLCommandBuilder()

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

    def wait_for_mysql_ready(
        self, max_attempts: Optional[int] = None, password: Optional[str] = None
    ) -> bool:
        """MySQL 서버가 준비될 때까지 대기"""
        if max_attempts is None:
            max_attempts = self.init_timeout

        print("\nMySQL 서버 준비 대기 중...")
        print(f"최대 대기 시간: {max_attempts}초")
        attempt = 0

        while attempt < max_attempts:
            try:
                if attempt % 5 == 0:
                    check_cmd = self.mysql_cmd.get_mysql_ping_command(
                        Constants.MYSQL_USER, password
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
                        print("\nMySQL 서버 준비 완료!")
                        return True

                    if "Access denied" in result.stderr and not password:
                        raise MySQLConfigError(
                            "MySQL 접속을 위한 비밀번호가 필요합니다."
                        )

                if attempt > 0 and attempt % 10 == 0:
                    print(f"\n{attempt}초 경과... (서버 초기화 중)")
                else:
                    print(".", end="", flush=True)

            except subprocess.CalledProcessError:
                pass

            time.sleep(1)
            attempt += 1

        raise MySQLConfigError(f"MySQL 서버 준비 시간 초과 ({max_attempts}초)")

    def execute_backup(
        self, db: str, password: str, table: Optional[str] = None
    ) -> bool:
        """백업 실행"""
        try:
            sql_dir = os.path.join(os.getcwd(), Constants.SQL_DIR)
            os.makedirs(sql_dir, exist_ok=True)

            if not self.is_container_running():
                self.start_container()

            self.wait_for_mysql_ready(password=password)

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
            raise MySQLConfigError(f"백업 실행 중 오류 발생: {str(e)}")

    def _perform_backup(
        self, backup_target: str, password: str, backup_path: str
    ) -> None:
        """백업 실행"""
        dump_cmd = self.mysql_cmd.get_mysqldump_command(
            Constants.MYSQL_USER, password, backup_target, backup_path
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
                raise MySQLConfigError("MySQL 비밀번호가 올바르지 않습니다.")
            raise MySQLConfigError(f"mysqldump 실행 중 오류: {stderr}")

    def _copy_backup_to_local(self, container_path: str, local_path: str) -> None:
        """백업 파일을 로컬로 복사"""
        subprocess.run(
            f"docker cp {self.container_name}:{container_path} {local_path}",
            shell=True,
            check=True,
        )


class VersionUpgradeManager(DockerContainerBase):
    """MySQL 버전 업그레이드 관리 클래스"""

    def __init__(self, container_name: str):
        super().__init__(container_name)
        self.password = None
        self.mysql_cmd = MySQLCommandBuilder()
        self.backup_manager = BackupManager(container_name)
        self.upgrade_paths = {"5.6": ["5.7", "8.0"], "5.7": ["8.0"], "8.0": []}

    def wait_for_mysql_ready(
        self, max_attempts: Optional[int] = None, password: Optional[str] = None
    ) -> bool:
        return self.backup_manager.wait_for_mysql_ready(max_attempts, password)

    def get_source_version(self) -> str:
        """원본 MySQL 버전 입력 받기"""
        while True:
            print("\n원본 데이터의 MySQL 버전을 선택하세요:")
            print("1. 5.6")
            print("2. 5.7")
            print("3. 8.0")
            choice = input("선택 (1-3): ").strip()

            version_map = {"1": "5.6", "2": "5.7", "3": "8.0"}
            if choice in version_map:
                return version_map[choice]
            print("잘못된 선택입니다.")

    def execute_upgrade(self, upgrade_path: List[str], password: str) -> bool:
        """업그레이드 실행"""
        try:
            self.password = password
            source_version = upgrade_path[0]
            target_version = upgrade_path[-1]

            try:
                if source_version == "5.6" and target_version.startswith("8.0"):
                    print("\n5.6 -> 5.7 -> 8.0 업그레이드 진행 중...")
                    self._upgrade_56_to_80(target_version, password)
                elif source_version == "5.6" and target_version.startswith("5.7"):
                    print(f"\n5.6 -> {target_version} 업그레이드 진행 중...")
                    self._upgrade_with_mysql_upgrade(password)
                elif source_version == "5.7" and target_version.startswith("8.0"):
                    print(f"\n5.7 -> {target_version} 업그레이드 진행 중...")
                    version_parts = target_version.split(".")
                    if len(version_parts) >= 3 and float(version_parts[2]) <= 15:
                        print(
                            "8.0.15 이하 버전으로 업그레이드 시 mysql_upgrade가 필요합니다..."
                        )
                        self.start_container()
                        self.wait_for_mysql_ready(password=password)
                        self._run_mysql_upgrade(password)
                    else:
                        print("8.0.16 이상 버전은 자동 업그레이드가 진행됩니다...")
                        self.start_container()
                        self.wait_for_mysql_ready(password=password)

                self.stop_container()
                return True

            except Exception as e:
                self.stop_container()
                raise MySQLConfigError(f"업그레이드 실행 중 오류 발생: {str(e)}")

        except Exception as e:
            cleanup_docker()
            raise MySQLConfigError(f"업그레이드 실행 중 오류 발생: {str(e)}")

    def _upgrade_56_to_80(self, target_version: str, password: str) -> None:
        """5.6에서 8.0으로 업그레이드"""
        try:
            # 1. 5.7 버전으로 중간 업그레이드
            print("\n===== 5.6 -> 8.0 업그레이드 1단계 =====")
            print(f"5.6 -> 5.7.44 버전으로 업그레이드를 진행합니다...")

            docker_manager = DockerComposeManager("5.7.44")
            docker_manager.create_config()

            self.start_container()
            self.wait_for_mysql_ready(password=password)
            self._run_mysql_upgrade(password)
            self.stop_container()

            # 2. 8.0 버전으로 최종 업그레이드
            print("\n===== 5.6 -> 8.0 업그레이드 2단계 =====")
            print(f"5.7.44 -> {target_version} 버전으로 업그레이드를 진행합니다...")

            version_parts = target_version.split(".")
            if len(version_parts) >= 3 and float(version_parts[2]) <= 15:
                print(
                    "8.0.15 이하 버전으로 업그레이드 시 mysql_upgrade가 필요합니다..."
                )
            else:
                print("8.0.16 이상 버전은 자동 업그레이드가 진행됩니다...")

            docker_manager = DockerComposeManager(target_version)
            docker_manager.create_config()

            self.start_container()
            self.wait_for_mysql_ready(password=password)

            # 8.0.15 이하 버전인 경우 mysql_upgrade 실행
            if len(version_parts) >= 3 and float(version_parts[2]) <= 15:
                self._run_mysql_upgrade(password)

        except Exception as e:
            self.stop_container()
            raise MySQLConfigError(f"5.6 -> 8.0 업그레이드 중 오류 발생: {str(e)}")

    def _upgrade_with_mysql_upgrade(self, password: str) -> None:
        """mysql_upgrade를 사용한 업그레이드"""
        try:
            self.start_container()
            self.wait_for_mysql_ready(password=password)
            self._run_mysql_upgrade(password)
        except Exception as e:
            self.stop_container()
            raise MySQLConfigError(f"mysql_upgrade 실행 중 오류 발생: {str(e)}")

    def _run_mysql_upgrade(self, password: str) -> None:
        """mysql_upgrade 스크립트 실행"""
        print("\nmysql_upgrade 실행 중...")
        pwd_option = f"-p'{password}'" if password else ""
        cmd = f"mysql_upgrade -u {Constants.MYSQL_USER} {pwd_option} --force"
        process = subprocess.Popen(
            ["docker", "container", "exec", self.container_name, "bash", "-c", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        stdout, stderr = process.communicate()

        if process.returncode != 0:
            if "Access denied" in stderr:
                raise MySQLConfigError("MySQL 비밀번호가 올바르지 않습니다.")
            raise MySQLConfigError(f"mysql_upgrade 실행 실패: {stderr}")

        if stdout:
            print("\nmysql_upgrade 출력:")
            print(stdout)

        print("\nmysql_upgrade 실행이 완료되었습니다.")


class VersionManager:
    """MySQL 버전 관리 클래스"""

    MYSQL_VERSIONS = {
        "5.6": [
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
        ],
        "5.7": [
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
        ],
        "8.0": [
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
        ],
    }

    @classmethod
    def display_versions(cls) -> List[str]:
        """버전 목록 표시"""
        try:
            all_versions = []
            version_count = 1

            print("\nMySQL 버전 선택:")
            print("=" * 120)

            for major_version, versions in cls.MYSQL_VERSIONS.items():
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
            raise MySQLConfigError(f"버전 표시 중 오류 발생: {str(e)}")

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
    cleanup_docker()  # 프로그램 종료 시 Docker 환경 정리
    exit(0)


def main():
    """메인 함수"""
    signal.signal(signal.SIGINT, signal_handler)

    try:
        # 데이터 디렉토리 생성
        os.makedirs(Constants.DATA_DIR, exist_ok=True)

        # 설정 관리자 초기화 및 설정 파일 체크
        config_manager = ConfigManager(Constants.CNF_PATH)
        config_manager.check_exists()

        # 도커에서 실행할 버전 선택 (타겟 버전)
        print("\n도커 컨테이너에서 실행할 MySQL 버전을 선택하세요:")
        versions = VersionManager.display_versions()
        target_version = VersionManager.select_version(versions)

        if target_version:
            # 버전별 소켓 경로 수정
            config_manager.modify_socket_path(target_version)

            # Docker Compose 설정 생성
            docker_manager = DockerComposeManager(target_version)
            docker_manager.create_config()

            # 백업 관리자 초기화
            backup_manager = BackupManager(docker_manager.container_name)

            print("\n작업을 선택하세요:")
            print("1. 컨테이너만 실행")
            print("2. 백업 진행")

            choice = input("선택 (1 또는 2): ").strip()

            if choice == "1":
                backup_manager.start_container()
                print("\n컨테이너 실행이 완료되었습니다.")
                print(
                    f"\n접속 방법: docker container exec -it {docker_manager.container_name} bash"
                )
                print("필수! 종료: docker compose down -v")

            elif choice == "2":
                # 원본 데이터의 버전 확인
                upgrade_manager = VersionUpgradeManager(docker_manager.container_name)
                source_version = upgrade_manager.get_source_version()
                target_version_major = ".".join(target_version.split(".")[:2])

                # 버전이 다른 경우 업그레이드 진행
                if source_version != target_version_major:
                    print(
                        f"\n버전 업그레이드가 필요합니다. ({source_version} -> {target_version})"
                    )
                    upgrade_path = [source_version, target_version]
                    print(f"업그레이드 경로: {' -> '.join(upgrade_path)}")

                    if input("\n업그레이드를 진행하시겠습니까? (y/N): ").lower() == "y":
                        backup_info = backup_manager.get_backup_target()
                        if backup_info:
                            db, table, password = backup_info
                            upgrade_manager.execute_upgrade(upgrade_path, password)
                            backup_manager.execute_backup(db, password, table)
                        else:
                            print("\n백업 대상 정보 입력이 취소되었습니다.")
                            exit(1)
                    else:
                        print(
                            "\n업그레이드를 진행 또는 같은 버전을 선택해주시길 바랍니다."
                        )
                        exit(1)
                else:
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

    except MySQLConfigError as e:
        print(f"\n설정 오류: {str(e)}")
        cleanup_docker()  # 오류 발생 시 Docker 환경 정리
        exit(1)
    except Exception as e:
        print(f"\n예상치 못한 오류: {str(e)}")
        cleanup_docker()  # 오류 발생 시 Docker 환경 정리
        exit(1)


if __name__ == "__main__":
    main()
