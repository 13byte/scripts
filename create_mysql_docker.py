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
            raise MySQLConfigError(
                f"복원에 필요한 MySQL 설정 파일({self.cnf_path})이 없습니다.\n"
                f"원본 서버의 설정 파일을 {self.cnf_path} 경로에 복사해주세요."
            )
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
      - {self.current_dir}/{CONSTANTS['CNF_PATH']}:/etc/my.cnf:ro
    ports:
      - "3306:3306"
    command: --defaults-file=/etc/my.cnf
    environment:
      - MYSQL_ALLOW_EMPTY_PASSWORD=yes
"""


class BackupManager:
    """백업 관리 클래스"""

    def __init__(self, container_name: str):
        self.container_name = container_name
        self.date_str = datetime.now().strftime("%Y%m%d")
        # 기본 타임아웃 설정
        self.init_timeout = 300  # 5분
        self.operation_timeout = 7200  # 2시간

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
        self, max_attempts: int = None, password: str = None
    ) -> bool:
        """MySQL 서버가 준비될 때까지 대기"""
        if max_attempts is None:
            max_attempts = self.init_timeout

        print("\nMySQL 서버 준비 대기 중...")
        print(f"최대 대기 시간: {max_attempts}초")
        attempt = 0

        while attempt < max_attempts:
            try:
                # password가 있는 경우에만 -p 옵션 추가
                pwd_option = f"-p'{password}'" if password else ""
                check_cmd = f"mysqladmin -u {CONSTANTS['MYSQL_USER']} {pwd_option} ping"
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

                # 서버가 준비되었고 접속 가능한 경우
                if result.returncode == 0 and "mysqld is alive" in result.stdout:
                    print("\nMySQL 서버 준비 완료!")
                    return True

                # 접근 거부된 경우 (비밀번호 필요)
                if "Access denied" in result.stderr:
                    if not password:
                        raise MySQLConfigError(
                            "MySQL 접속을 위한 비밀번호가 필요합니다."
                        )

            except subprocess.CalledProcessError:
                pass

            # 진행 상태 표시 개선
            if attempt > 0 and attempt % 10 == 0:
                print(f"\n{attempt}초 경과... (서버 초기화 중)")
            else:
                print(".", end="", flush=True)

            time.sleep(1)
            attempt += 1

        raise MySQLConfigError(f"MySQL 서버 준비 시간 초과 ({max_attempts}초)")

    def execute_backup(
        self, db: str, password: str, table: Optional[str] = None
    ) -> bool:
        """백업 실행"""
        try:
            sql_dir = os.path.join(os.getcwd(), CONSTANTS["SQL_DIR"])
            os.makedirs(sql_dir, exist_ok=True)

            print("\nDocker 컨테이너 시작 중...")
            subprocess.run(["docker", "compose", "up", "-d"], check=True)

            # 버전 업그레이드 감지 및 타임아웃 조정
            current_version = self._get_current_version()
            if current_version:
                timeout = self._calculate_timeout(current_version)
                self.wait_for_mysql_ready(timeout, password)
            else:
                self.wait_for_mysql_ready(password=password)

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

    def _get_current_version(self) -> Optional[str]:
        """현재 MySQL 버전 확인"""
        try:
            cmd = f"mysql -u {CONSTANTS['MYSQL_USER']} -N -B -e 'SELECT VERSION();'"
            result = subprocess.run(
                ["docker", "container", "exec", self.container_name, "bash", "-c", cmd],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except:
            return None

    def _calculate_timeout(self, version: str) -> int:
        """버전과 데이터 크기에 따른 타임아웃 계산"""
        try:
            # 데이터 디렉토리 크기 확인 (GB 단위)
            du_cmd = f"du -s {CONSTANTS['DATA_DIR']}"
            result = subprocess.run(du_cmd.split(), capture_output=True, text=True)
            size_kb = int(result.stdout.split()[0])
            size_gb = size_kb / (1024 * 1024)

            # 기본 타임아웃: 5분
            base_timeout = 300

            # 데이터 크기별 타임아웃 계산 (비선형적 증가)
            if size_gb < 1:
                timeout = base_timeout
            elif size_gb < 10:
                timeout = base_timeout * 2
            elif size_gb < 50:
                timeout = base_timeout * 4
            elif size_gb < 100:
                timeout = base_timeout * 8
            else:
                timeout = base_timeout * 12

            # 버전 업그레이드에 따른 추가 시간
            if self._is_version_upgrade(version):
                timeout *= 1.5  # 50% 추가 시간

            return min(int(timeout), 7200)  # 최대 2시간으로 제한
        except Exception as e:
            print(f"타임아웃 계산 중 오류 발생: {str(e)}")
            return self.init_timeout  # 에러 발생 시 기본값 사용

    def _is_version_upgrade(self, current_version: str) -> bool:
        """버전 업그레이드 여부 확인"""
        try:
            # 설정 파일에서 이전 버전 정보 추출
            with open(CONSTANTS["CNF_PATH"], "r") as f:
                content = f.read()
                match = re.search(r"version\s*=\s*(\d+\.\d+\.\d+)", content)
                if match:
                    old_version = match.group(1)
                    return self._compare_versions(old_version, current_version)
            return False
        except:
            return False

    def _compare_versions(self, old: str, new: str) -> bool:
        """버전 비교"""
        try:
            old_parts = [int(x) for x in old.split(".")]
            new_parts = [int(x) for x in new.split(".")]
            return new_parts > old_parts
        except:
            return False


class BackupManager:
    """백업 관리 클래스"""

    def __init__(self, container_name: str):
        self.container_name = container_name
        self.date_str = datetime.now().strftime("%Y%m%d")
        self.init_timeout = 300  # 5분
        self.operation_timeout = 7200  # 2시간
        self.current_version = None

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
        self, max_attempts: int = None, password: str = None
    ) -> bool:
        """MySQL 서버가 준비될 때까지 대기"""
        if max_attempts is None:
            max_attempts = self.init_timeout

        print("\nMySQL 서버 준비 대기 중...")
        print(f"최대 대기 시간: {max_attempts}초")
        attempt = 0

        while attempt < max_attempts:
            try:
                # password가 있는 경우에만 -p 옵션 추가
                pwd_option = f"-p'{password}'" if password else ""
                check_cmd = f"mysqladmin -u {CONSTANTS['MYSQL_USER']} {pwd_option} ping"
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

                # 서버가 준비되었고 접속 가능한 경우
                if result.returncode == 0 and "mysqld is alive" in result.stdout:
                    print("\nMySQL 서버 준비 완료!")
                    return True

                # 접근 거부된 경우 (비밀번호 필요)
                if "Access denied" in result.stderr:
                    if not password:
                        raise MySQLConfigError(
                            "MySQL 접속을 위한 비밀번호가 필요합니다."
                        )

            except subprocess.CalledProcessError:
                pass

            # 진행 상태 표시 개선
            if attempt > 0 and attempt % 10 == 0:
                print(f"\n{attempt}초 경과... (서버 초기화 중)")
            else:
                print(".", end="", flush=True)

            time.sleep(1)
            attempt += 1

        raise MySQLConfigError(f"MySQL 서버 준비 시간 초과 ({max_attempts}초)")

    def start_container(self, password: str = None) -> bool:
        """Docker 컨테이너 시작"""
        try:
            print("\nDocker 컨테이너 시작 중...")
            subprocess.run(["docker", "compose", "up", "-d"], check=True)

            # 현재 버전 확인
            self.current_version = self._get_current_version()
            if self.current_version:
                timeout = self._calculate_timeout(self.current_version)
                self.wait_for_mysql_ready(timeout, password)
            else:
                self.wait_for_mysql_ready(password=password)

            print("\n컨테이너가 성공적으로 시작되었습니다.")
            return True

        except Exception as e:
            raise MySQLConfigError(f"컨테이너 시작 중 오류 발생: {str(e)}")

    def execute_backup(
        self, db: str, password: str, table: Optional[str] = None
    ) -> bool:
        """백업 실행"""
        try:
            sql_dir = os.path.join(os.getcwd(), CONSTANTS["SQL_DIR"])
            os.makedirs(sql_dir, exist_ok=True)

            # 컨테이너가 실행되어 있지 않다면 시작
            if not self._is_container_running():
                self.start_container(password)

            backup_target = f"{db}.{table}" if table else db
            backup_file = f"{self.date_str}_{backup_target.replace('.', '_')}.sql"
            container_backup_path = f"{CONSTANTS['BACKUP_DIR']}/{backup_file}"
            local_backup_path = os.path.join(sql_dir, backup_file)

            print(f"\n{backup_target} 백업 중...")
            self._perform_backup(backup_target, password, container_backup_path)
            self._copy_backup_to_local(container_backup_path, local_backup_path)

            print("\n컨테이너 정지 중...")
            subprocess.run(["docker", "compose", "stop"], check=True)

            print(f"\n백업 완료: {CONSTANTS['SQL_DIR']}/{backup_file}")
            return True

        except Exception as e:
            raise MySQLConfigError(f"백업 실행 중 오류 발생: {str(e)}")

    def _is_container_running(self) -> bool:
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

    def _perform_backup(
        self, backup_target: str, password: str, backup_path: str
    ) -> None:
        """백업 실행"""
        dump_cmd = f"mysqldump -u {CONSTANTS['MYSQL_USER']} -p'{password}' {backup_target} > {backup_path}"
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

    def _get_current_version(self) -> Optional[str]:
        """현재 MySQL 버전 확인"""
        try:
            cmd = f"mysql -u {CONSTANTS['MYSQL_USER']} -N -B -e 'SELECT VERSION();'"
            result = subprocess.run(
                ["docker", "container", "exec", self.container_name, "bash", "-c", cmd],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except:
            return None

    def _calculate_timeout(self, version: str) -> int:
        """버전과 데이터 크기에 따른 타임아웃 계산"""
        try:
            # 데이터 디렉토리 크기 확인 (GB 단위)
            du_cmd = f"du -s {CONSTANTS['DATA_DIR']}"
            result = subprocess.run(du_cmd.split(), capture_output=True, text=True)
            size_kb = int(result.stdout.split()[0])
            size_gb = size_kb / (1024 * 1024)

            # 기본 타임아웃: 5분
            base_timeout = 300

            # 데이터 크기별 타임아웃 계산 (비선형적 증가)
            if size_gb < 1:
                timeout = base_timeout
            elif size_gb < 10:
                timeout = base_timeout * 2
            elif size_gb < 50:
                timeout = base_timeout * 4
            elif size_gb < 100:
                timeout = base_timeout * 8
            else:
                timeout = base_timeout * 12

            # 버전 차이에 따른 추가 시간 계산
            if self.current_version:  # current_version이 있는 경우에만 계산
                version_difference = self._calculate_version_difference(
                    self.current_version, version
                )

                # 메이저 버전 차이가 있는 경우 (예: 5.6 -> 8.0)
                if version_difference["major"] > 0:
                    timeout *= 1.5 * version_difference["major"]
                # 마이너 버전 차이가 있는 경우 (예: 5.6 -> 5.7)
                elif version_difference["minor"] > 0:
                    timeout *= 1.2 * version_difference["minor"]
                # 패치 버전 차이만 있는 경우 (예: 5.6.48 -> 5.6.51)
                elif (
                    version_difference["patch"] > 10
                ):  # 패치 버전 차이가 10 이상인 경우
                    timeout *= 1.1

            return min(int(timeout), 7200)  # 최대 2시간으로 제한
        except Exception as e:
            print(f"타임아웃 계산 중 오류 발생: {str(e)}")
            return self.init_timeout  # 에러 발생 시 기본값 사용

    def _calculate_version_difference(self, old_version: str, new_version: str) -> dict:
        """버전 차이 계산"""
        try:
            old_parts = [int(x) for x in old_version.split(".")]
            new_parts = [int(x) for x in new_version.split(".")]

            # 메이저 버전 차이 (첫 번째 숫자)
            major_diff = abs(new_parts[0] - old_parts[0])

            # 마이너 버전 차이 (두 번째 숫자)
            minor_diff = abs(new_parts[1] - old_parts[1])

            # 패치 버전 차이 (세 번째 숫자)
            patch_diff = abs(new_parts[2] - old_parts[2])

            return {"major": major_diff, "minor": minor_diff, "patch": patch_diff}
        except:
            return {"major": 0, "minor": 0, "patch": 0}


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

    @staticmethod
    def display_versions() -> List[str]:
        """버전 목록 표시"""
        try:
            all_versions = []
            version_count = 1

            print("\nMySQL 버전 선택:")
            print("=" * 120)

            for major_version, versions in VersionManager.MYSQL_VERSIONS.items():
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
        target_version = VersionManager.select_version(versions)

        if target_version:
            # 버전 업그레이드 관리자 초기화
            upgrade_manager = VersionUpgradeManager(
                f"mysql_{target_version.replace('.', '_')}"
            )
            current_version = upgrade_manager.get_current_version()

            # 업그레이드 필요 여부 확인
            if current_version != target_version:
                print("\n버전 업그레이드가 필요합니다.")
                upgrade_path = upgrade_manager.plan_upgrade_path(target_version)
                print(f"업그레이드 경로: {' -> '.join(upgrade_path)}")

                if input("\n업그레이드를 진행하시겠습니까? (y/N): ").lower() == "y":
                    upgrade_manager.execute_upgrade(upgrade_path)

            # Docker Compose 설정 생성
            docker_manager = DockerComposeManager(target_version)

            if docker_manager.create_config():
                # 백업 관리자 초기화
                backup_manager = BackupManager(docker_manager.container_name)

                print("\n작업을 선택하세요:")
                print("1. 컨테이너만 실행")
                print("2. 백업 진행")

                choice = input("선택 (1 또는 2): ").strip()

                if choice == "1":
                    backup_manager.start_container()
                    print("\n컨테이너 실행이 완료되었습니다.")
                elif choice == "2":
                    backup_info = backup_manager.get_backup_target()
                    if backup_info:
                        db, table, password = backup_info
                        backup_manager.execute_backup(db, password, table)
                    print("\n모든 작업이 완료되었습니다.")
                else:
                    print("\n잘못된 선택입니다.")
                    exit(1)

    except MySQLConfigError as e:
        print(f"\n설정 오류: {str(e)}")
        exit(1)
    except Exception as e:
        print(f"\n예상치 못한 오류: {str(e)}")
        exit(1)


if __name__ == "__main__":
    main()
