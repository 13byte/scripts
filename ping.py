import asyncio
import platform
import datetime
from typing import List, Dict, Set, Optional, Tuple
import sys
import os
import signal
import resource
from dataclasses import dataclass
from collections import deque


@dataclass
class PingResult:
    host: str
    success: bool
    response_time: float
    timestamp: datetime.datetime


@dataclass
class MonitorSettings:
    interval: int
    failure_threshold: int
    recovery_threshold: int


def optimize_system_settings():
    """시스템 설정 최적화"""
    try:
        # 파일 디스크립터 제한 증가
        resource.setrlimit(resource.RLIMIT_NOFILE, (65535, 65535))

        # TCP 커널 파라미터 최적화 시도 (root 권한 필요)
        os.system("sysctl -w net.ipv4.tcp_tw_reuse=1 > /dev/null 2>&1")
        os.system("sysctl -w net.ipv4.tcp_fin_timeout=30 > /dev/null 2>&1")
    except Exception:
        pass

    try:
        # 프로세스 우선순위 설정 시도
        os.nice(-10)  # 높은 우선순위 설정 (root 권한 필요)
    except Exception:
        pass


async def execute_ping(host: str) -> Tuple[bool, float]:
    """최적화된 ping 실행"""
    is_darwin = platform.system() == "Darwin"

    if is_darwin:
        cmd = ["ping", "-c", "1", "-W", "1000", "-q", host]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", "-n", "-q", host]

    try:
        start_time = datetime.datetime.now()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=os.setsid,
        )

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.0)
            if proc.returncode == 0:
                for line in stdout.decode().split("\n"):
                    if "min/avg/max" in line:
                        avg_time = float(line.split("/")[4])
                        return True, avg_time
            return False, 0.0

        except asyncio.TimeoutError:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            return False, 0.0

    except Exception:
        return False, 0.0


class PingMonitor:
    def __init__(self, filename: str, settings: MonitorSettings):
        self.filename = filename
        self.settings = settings
        self.groups: Dict[str, List[str]] = {}
        self.running = True
        self.clear_command = "clear"
        self.status_history: Dict[str, Dict] = {}
        self.history_count = 3
        self.current_history_index: Dict[str, int] = {}
        self.consecutive_failures: Dict[str, int] = {}
        self.consecutive_successes: Dict[str, int] = {}
        self.batch_size = 50  # M 시리즈 성능 고려
        self.results_cache: Dict[str, deque] = {}
        self.max_history_entries = 1000
        self.changed_hosts: Set[str] = set()
        self.semaphore = asyncio.Semaphore(200)  # 동시 실행 제한

        # 호스트 주소 캐시
        self._host_cache: Dict[str, str] = {}

    async def preload_dns(self):
        """DNS 미리 로드"""
        for group in self.groups.values():
            for host in group:
                if host not in self._host_cache:
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            "getent", "hosts", host, stdout=asyncio.subprocess.PIPE
                        )
                        stdout, _ = await proc.communicate()
                        if stdout:
                            ip = stdout.decode().split()[0]
                            self._host_cache[host] = ip
                    except Exception:
                        self._host_cache[host] = host

    def clear_screen(self):
        os.system(self.clear_command)

    def sort_ip_addresses(self, ips: List[str]) -> List[str]:
        def ip_to_tuple(ip: str) -> tuple:
            try:
                parts = ip.split(".")
                return tuple(int(part) for part in parts)
            except (ValueError, IndexError):
                return (999, 999, 999, 999)

        return sorted(ips, key=ip_to_tuple)

    def read_groups(self) -> None:
        current_group = "기본그룹"
        self.groups = {current_group: []}
        seen_ips = set()

        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        current_group = line[1:-1]
                        self.groups[current_group] = []
                    elif line:
                        self.groups[current_group].append(line)
                        if line not in seen_ips:
                            self.initialize_host_status(line)
                            seen_ips.add(line)

            for group in self.groups:
                self.groups[group] = self.sort_ip_addresses(self.groups[group])

        except FileNotFoundError:
            print(f"Error: {self.filename} 파일을 찾을 수 없습니다.")
            sys.exit(1)

    def initialize_host_status(self, host: str) -> None:
        self.status_history[host] = {
            "last_status": None,
            "history": [{"down": None, "up": None} for _ in range(self.history_count)],
        }
        self.current_history_index[host] = 0
        self.consecutive_failures[host] = 0
        self.consecutive_successes[host] = 0
        self.results_cache[host] = deque(maxlen=self.max_history_entries)

    async def ping_server(self, host: str) -> PingResult:
        async with self.semaphore:
            # 캐시된 IP 사용
            target_host = self._host_cache.get(host, host)
            success, response_time = await execute_ping(target_host)
            result = PingResult(host, success, response_time, datetime.datetime.now())
            self.update_host_status(result)
            return result

    def update_host_status(self, result: PingResult) -> None:
        host = result.host
        success = result.success
        current_status = self.status_history[host]["last_status"]

        if not success:
            self.consecutive_failures[host] += 1
            self.consecutive_successes[host] = 0
            if (
                self.consecutive_failures[host] >= self.settings.failure_threshold
                and current_status is not False
            ):
                self.update_failure_status(host, result.timestamp)
                self.changed_hosts.add(host)
        else:
            self.consecutive_successes[host] += 1
            if current_status is False:
                if self.consecutive_successes[host] >= self.settings.recovery_threshold:
                    self.update_recovery_status(host, result.timestamp)
                    self.changed_hosts.add(host)
                    self.consecutive_failures[host] = 0
            elif current_status is None:
                self.status_history[host]["last_status"] = True
                self.consecutive_failures[host] = 0

        self.results_cache[host].append(result)

    def update_failure_status(self, host: str, timestamp: datetime.datetime) -> None:
        current_idx = self.current_history_index[host]
        self.status_history[host]["history"][current_idx] = {
            "down": timestamp,
            "up": None,
        }
        self.status_history[host]["last_status"] = False

    def update_recovery_status(self, host: str, timestamp: datetime.datetime) -> None:
        current_idx = self.current_history_index[host]
        if self.status_history[host]["history"][current_idx]["down"] is not None:
            self.status_history[host]["history"][current_idx]["up"] = timestamp
            self.current_history_index[host] = (current_idx + 1) % self.history_count
        self.status_history[host]["last_status"] = True

    async def ping_batch(self, hosts: List[str]) -> List[PingResult]:
        tasks = [self.ping_server(host) for host in hosts]
        return await asyncio.gather(*tasks)

    async def ping_all_groups(self) -> Dict[str, List[PingResult]]:
        group_results = {}
        for group_name, hosts in self.groups.items():
            results = await self.ping_batch(hosts)
            group_results[group_name] = results
        return group_results

    def format_time(self, dt: Optional[datetime.datetime]) -> str:
        return "-" if dt is None else dt.strftime("%H:%M:%S")

    def get_display_width(self, text: str) -> int:
        return sum(
            (
                2
                if (0xAC00 <= ord(char) <= 0xD7A3) or (0x4E00 <= ord(char) <= 0x9FFF)
                else 1
            )
            for char in text
        )

    def pad_string(self, text: str, width: int, align="left") -> str:
        display_width = self.get_display_width(text)
        padding = width - display_width

        if align == "left":
            return text + " " * padding
        elif align == "right":
            return " " * padding + text
        else:
            left_padding = padding // 2
            right_padding = padding - left_padding
            return " " * left_padding + text + " " * right_padding

    def format_results(self, group_results: Dict[str, List[PingResult]]) -> str:
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output = []

        WIDTHS = {
            "서버": 15,
            "상태": 7,
            "응답시간": 10,
            **{f"{i}차끊김": 10 for i in range(1, self.history_count + 1)},
            **{f"{i}차복구": 10 for i in range(1, self.history_count + 1)},
        }

        headers = ["서버", "상태", "응답시간"]
        for i in range(1, self.history_count + 1):
            headers.extend([f"{i}차끊김", f"{i}차복구"])

        total_width = sum(WIDTHS.values()) + (2 * (len(headers) - 1))

        output.append("=" * total_width)
        output.append(f"Ping 모니터링 현황 (마지막 업데이트: {current_time})")
        output.append(
            f"설정: 간격 {self.settings.interval}초, 실패 기준 {self.settings.failure_threshold}회, "
            f"복구 기준 {self.settings.recovery_threshold}회"
        )
        output.append("=" * total_width)

        for group_name, results in group_results.items():
            if not results:
                continue

            output.append(f"\n=== {group_name} ===")
            header_row = [self.pad_string(header, WIDTHS[header]) for header in headers]
            output.append("  ".join(header_row))
            output.append("-" * total_width)

            group_success = 0
            for result in results:
                row = self.format_result_row(result, WIDTHS)
                output.append("  ".join(row))
                if result.success:
                    group_success += 1

            success_rate = (group_success / len(results)) * 100
            output.append(
                f"\n그룹 연결 상태: {group_success}/{len(results)} ({success_rate:.1f}%)"
            )

        return "\n".join(output)

    def format_result_row(
        self, result: PingResult, widths: Dict[str, int]
    ) -> List[str]:
        row = []

        status = "O" if result.success else "X"
        response_str = f"{result.response_time:.1f}ms" if result.success else "timeout"

        row.append(self.pad_string(result.host, widths["서버"]))
        row.append(self.pad_string(status, widths["상태"]))
        row.append(self.pad_string(response_str, widths["응답시간"]))

        history = self.status_history[result.host]["history"]
        for i in range(self.history_count):
            entry = history[i]
            row.append(
                self.pad_string(self.format_time(entry["down"]), widths[f"{i+1}차끊김"])
            )
            row.append(
                self.pad_string(self.format_time(entry["up"]), widths[f"{i+1}차복구"])
            )

        return row

    async def monitor_all_groups(self):
        try:
            # DNS 미리 로드
            await self.preload_dns()

            while self.running:
                start_time = datetime.datetime.now()

                group_results = await self.ping_all_groups()
                self.clear_screen()
                print(self.format_results(group_results))

                # 정확한 간격 유지를 위한 sleep 계산
                elapsed = (datetime.datetime.now() - start_time).total_seconds()
                remaining = max(0.0, self.settings.interval - elapsed)
                if remaining > 0:
                    await asyncio.sleep(remaining)

        except KeyboardInterrupt:
            self.running = False
        finally:
            self.cleanup()

    def cleanup(self):
        """리소스 정리"""
        for host in self.results_cache:
            self.results_cache[host].clear()
        self.results_cache.clear()
        self.status_history.clear()
        self._host_cache.clear()


def get_monitor_settings() -> MonitorSettings:
    while True:
        try:
            print("\n=== Ping 모니터링 설정 ===")
            interval = int(input("Ping 간격을 입력하세요 (초 단위, 1-60): "))
            if not 1 <= interval <= 60:
                print("Ping 간격은 1초에서 60초 사이여야 합니다.")
                continue

            failure_threshold = int(input("실패 판단 기준 횟수를 입력하세요 (1-10): "))
            if not 1 <= failure_threshold <= 10:
                print("실패 판단 기준 횟수는 1회에서 10회 사이여야 합니다.")
                continue

            recovery_threshold = int(input("복구 판단 기준 횟수를 입력하세요 (1-10): "))
            if not 1 <= recovery_threshold <= 10:
                print("복구 판단 기준 횟수는 1회에서 10회 사이여야 합니다.")
                continue

            return MonitorSettings(interval, failure_threshold, recovery_threshold)

        except ValueError:
            print("올바른 숫자를 입력해주세요.")


def main():
    try:
        # 시스템 설정 최적화
        optimize_system_settings()

        # 모니터링 설정 입력 받기
        settings = get_monitor_settings()

        # PingMonitor 인스턴스 생성 및 실행
        monitor = PingMonitor("target_ip.txt", settings)
        monitor.read_groups()

        # 이벤트 루프 실행
        asyncio.run(monitor.monitor_all_groups())

    except KeyboardInterrupt:
        print("\n프로그램을 종료합니다.")
    except Exception as e:
        print(f"\n오류가 발생했습니다: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
