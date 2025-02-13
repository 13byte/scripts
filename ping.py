import asyncio
import platform
import datetime
import time
from typing import List, Dict, Set, Optional, Tuple, DefaultDict
import sys
import os
import signal
import resource
from dataclasses import dataclass
from collections import deque, defaultdict
import gc
import subprocess
from concurrent.futures import ThreadPoolExecutor


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


class LRUCache:
    def __init__(self, capacity: int):
        self.cache = {}
        self.capacity = capacity
        self.usage = deque()

    def get(self, key):
        if key not in self.cache:
            return None
        self.usage.remove(key)
        self.usage.append(key)
        return self.cache[key]

    def put(self, key, value):
        if key in self.cache:
            self.usage.remove(key)
        elif len(self.cache) >= self.capacity:
            oldest = self.usage.popleft()
            del self.cache[oldest]
        self.cache[key] = value
        self.usage.append(key)


class ProcessManager:
    def __init__(self, max_concurrent=350):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.active_processes = set()
        self.buffer_size = 65535
        self.ping_timeout = 0.5  # 타임아웃 시간 감소
        self.executor = ThreadPoolExecutor(max_workers=max_concurrent)

    async def execute_ping(self, ip: str) -> Tuple[bool, float]:
        async with self.semaphore:
            is_darwin = platform.system() == "Darwin"

            if is_darwin:
                cmd = ["ping", "-c", "1", "-W", "500", "-q", ip]  # 타임아웃 500ms
            else:
                cmd = ["ping", "-c", "1", "-w", "0.5", "-q", ip]  # 타임아웃 0.5초

            try:
                start_time = time.monotonic()
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    preexec_fn=os.setsid,
                )
                self.active_processes.add(proc)

                try:
                    stdout, _ = await asyncio.wait_for(
                        proc.communicate(), timeout=0.6
                    )  # 약간 더 긴 타임아웃
                    elapsed = time.monotonic() - start_time
                    if proc.returncode == 0:
                        try:
                            stdout_str = stdout.decode()
                            if "min/avg/max" in stdout_str:
                                avg_time = float(stdout_str.split("/")[4])
                                return True, avg_time
                            elif "time=" in stdout_str:
                                time_str = (
                                    stdout_str.split("time=")[1]
                                    .split()[0]
                                    .replace("ms", "")
                                )
                                return True, float(time_str)
                            return True, elapsed * 1000
                        except (IndexError, ValueError):
                            return True, elapsed * 1000
                    return False, 0.0
                except asyncio.TimeoutError:
                    return False, 0.0
                finally:
                    if proc in self.active_processes:
                        self.active_processes.remove(proc)
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        except ProcessLookupError:
                            pass
            except Exception as e:
                print(f"Error pinging {ip}: {e}")
                return False, 0.0

    async def cleanup(self):
        for proc in list(self.active_processes):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.active_processes.clear()
        self.executor.shutdown(wait=False)


class ResultProcessor:
    def __init__(self, cache_size=1000):
        self.cache = LRUCache(cache_size)
        self.last_results = {}
        self.statistics: DefaultDict[str, Dict] = defaultdict(
            lambda: {"success": 0, "total": 0, "last_status": None}
        )

    def process_result(self, result: PingResult) -> None:
        self.cache.put(result.host, result)
        self.statistics[result.host]["total"] += 1
        if result.success:
            self.statistics[result.host]["success"] += 1
        self.statistics[result.host]["last_status"] = result.success
        self.last_results[result.host] = result

    def get_host_stats(self, host: str) -> Dict:
        stats = self.statistics[host]
        total = stats["total"]
        if total == 0:
            return {"success_rate": 0.0, "last_status": None}
        return {
            "success_rate": (stats["success"] / total) * 100,
            "last_status": stats["last_status"],
        }


class MemoryOptimizer:
    def __init__(self):
        self.gc_interval = 10
        self.iteration = 0
        gc.disable()

    def optimize(self):
        self.iteration += 1
        if self.iteration % self.gc_interval == 0:
            gc.collect()

    def cleanup(self):
        gc.enable()


def optimize_system_settings():
    try:
        # 파일 디스크립터 및 프로세스 제한 증가
        resource.setrlimit(resource.RLIMIT_NOFILE, (65535, 65535))
        resource.setrlimit(resource.RLIMIT_NPROC, (4096, 4096))

        if platform.system() != "Darwin":
            settings = [
                "net.ipv4.tcp_tw_reuse=1",
                "net.ipv4.tcp_fin_timeout=15",
                "net.core.rmem_max=26214400",
                "net.core.wmem_max=26214400",
                "net.ipv4.tcp_rmem='4096 87380 16777216'",
                "net.ipv4.tcp_wmem='4096 87380 16777216'",
                "net.ipv4.ip_local_port_range='1024 65535'",
                "net.ipv4.tcp_max_tw_buckets=1440000",
                "net.core.somaxconn=1024",
                "net.ipv4.tcp_max_syn_backlog=1024",
                "net.ipv4.tcp_synack_retries=2",
                "net.ipv4.tcp_syn_retries=2",
                "net.ipv4.ping_group_range='0 2147483647'",
            ]
            for setting in settings:
                os.system(f"sysctl -w {setting} > /dev/null 2>&1")

            try:
                # 실시간 스케줄링 우선순위 설정
                param = os.sched_param(os.SCHED_RR, 50)
                os.sched_setscheduler(0, os.SCHED_RR, param)
            except Exception:
                pass

        # 프로세스 우선순위 설정
        try:
            os.nice(-10)
        except Exception:
            pass

    except Exception as e:
        print(f"Warning: System optimization failed: {e}")


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
        self.results_cache: Dict[str, deque] = {}
        self.max_history_entries = 100
        self.changed_hosts: Set[str] = set()

        # 최적화된 컴포넌트들
        self.process_manager = ProcessManager(max_concurrent=350)
        self.result_processor = ResultProcessor(cache_size=1000)
        self.memory_optimizer = MemoryOptimizer()
        self.executor = ThreadPoolExecutor(max_workers=4)

    async def clear_screen(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.executor, os.system, self.clear_command)

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

    async def ping_server(self, ip: str) -> PingResult:
        success, response_time = await self.process_manager.execute_ping(ip)
        result = PingResult(ip, success, response_time, datetime.datetime.now())
        self.update_host_status(result)
        self.result_processor.process_result(result)
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
            # 성공할 때마다 실패 카운터 초기화
            self.consecutive_failures[host] = 0
            self.consecutive_successes[host] += 1

            if current_status is False:
                if self.consecutive_successes[host] >= self.settings.recovery_threshold:
                    self.update_recovery_status(host, result.timestamp)
                    self.changed_hosts.add(host)
            elif current_status is None:
                self.status_history[host]["last_status"] = True

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

    async def format_results(self, group_results: Dict[str, List[PingResult]]) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.executor, self._format_results_sync, group_results
        )

    def _format_results_sync(self, group_results: Dict[str, List[PingResult]]) -> str:
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
            last_update_time = time.monotonic()
            screen_update_counter = 0

            while self.running:
                current_time = time.monotonic()
                elapsed = current_time - last_update_time

                if elapsed < self.settings.interval:
                    await asyncio.sleep(0.1)  # 짧은 간격으로 체크
                    continue

                try:
                    group_results = await self.ping_all_groups()

                    # 화면 업데이트 최적화 (5회마다 화면 지우기)
                    screen_update_counter += 1
                    if screen_update_counter >= 5:
                        await self.clear_screen()
                        screen_update_counter = 0

                    formatted_results = await self.format_results(group_results)
                    print(
                        "\033[2J\033[H" + formatted_results
                    )  # ANSI 이스케이프 코드 사용

                    self.memory_optimizer.optimize()

                except Exception as e:
                    print(f"Error during monitoring: {e}")

                last_update_time = current_time  # 실제 실행 완료 시간 기준으로 업데이트

        except asyncio.CancelledError:
            self.running = False
        finally:
            self.memory_optimizer.cleanup()
            await self.cleanup()

    async def cleanup(self):
        """리소스 정리"""
        await self.process_manager.cleanup()
        self.executor.shutdown(wait=False)
        for host in self.results_cache:
            self.results_cache[host].clear()
        self.results_cache.clear()
        self.status_history.clear()


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


async def main():
    try:
        # 시스템 설정 최적화
        optimize_system_settings()

        # 모니터링 설정 입력 받기
        settings = get_monitor_settings()

        # PingMonitor 인스턴스 생성 및 실행
        monitor = PingMonitor("target_ip.txt", settings)
        monitor.read_groups()

        # 모니터링 시작
        await monitor.monitor_all_groups()

    except KeyboardInterrupt:
        print("\n프로그램을 종료합니다.")
    except Exception as e:
        print(f"\n오류가 발생했습니다: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        import uvloop

        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except ImportError:
        pass

    asyncio.run(main())  # 이 줄을 추가
