#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import pandas as pd
import os
import sys
import glob
from collections import defaultdict


def analyze_bot_requests(log_files):
    """
    여러 로그 파일에서 봇 요청을 분석
    """
    # 봇 패턴 정의
    bot_patterns = {
        "Googlebot": r"googlebot",
        "Bingbot": r"bingbot",
        "Yandex": r"yandex",
        "Baidu": r"baiduspider",
        "Ahrefs": r"ahrefs",
        "Semrush": r"semrush",
        "DuckDuckGo": r"duckduckbot",
        "Yahoo": r"yahoo! slurp",
        "Claude": r"claude",
        "Anthropic": r"anthropic",
        "ChatGPT": r"chatgpt",
        "OpenAI": r"openai",
        "AdsBot-Google": r"adsbot-google",
        "Sogou": r"sogou",
        "Facebook": r"facebookexternalhit",
        "Twitter": r"twitterbot",
        "Applebot": r"applebot",
        "NaverBot": r"naverbot",
        "DaumBot": r"daumoa",
        "기타 봇": r"bot|spider|crawler|archiver",
    }

    # 결과 저장용 카운터
    bot_counter = defaultdict(int)
    total_lines = 0
    bot_lines = 0

    # 로그 파일 분석
    for log_file in log_files:
        print(f"분석 중: {log_file}")
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    total_lines += 1

                    # User-Agent 추출
                    ua_match = re.search(r'"([^"]*)"$', line)
                    if ua_match:
                        user_agent = ua_match.group(1).lower()
                        bot_found = False

                        # 각 봇 패턴 확인 (기타 봇 제외)
                        for bot_name, pattern in bot_patterns.items():
                            if bot_name == "기타 봇":
                                continue

                            if re.search(pattern, user_agent, re.IGNORECASE):
                                bot_counter[bot_name] += 1
                                bot_found = True
                                bot_lines += 1
                                break

                        # 기타 봇 확인
                        if not bot_found and re.search(
                            bot_patterns["기타 봇"], user_agent, re.IGNORECASE
                        ):
                            bot_counter["기타 봇"] += 1
                            bot_lines += 1
        except Exception as e:
            print(f"오류: {log_file} 처리 중 - {e}")

    # 결과 데이터프레임 생성
    results = []
    for bot_name, count in bot_counter.items():
        if count > 0:  # 카운트가 있는 봇만 포함
            results.append({"봇 이름": bot_name, "요청 수": count})

    # 요청 수 기준으로 정렬
    results = sorted(results, key=lambda x: x["요청 수"], reverse=True)

    # 합계 추가
    results.append({"봇 이름": "총 봇 요청", "요청 수": bot_lines})
    results.append({"봇 이름": "전체 요청", "요청 수": total_lines})
    results.append(
        {
            "봇 이름": "봇 비율 (%)",
            "요청 수": (
                round(bot_lines / total_lines * 100, 2) if total_lines > 0 else 0
            ),
        }
    )

    return pd.DataFrame(results)


def save_results(df, output_format="csv"):
    """
    결과를 CSV 또는 Excel로 저장
    """
    if output_format.lower() == "xlsx":
        try:
            df.to_excel("bot_analysis_results.xlsx", index=False)
            print("결과가 bot_analysis_results.xlsx에 저장되었습니다.")
            return True
        except Exception as e:
            print(f"Excel 저장 오류: {e}")
            output_format = "csv"  # 문제 발생시 CSV로 대체

    # CSV로 저장
    df.to_csv("bot_analysis_results.csv", index=False)
    print("결과가 bot_analysis_results.csv에 저장되었습니다.")
    return True


def main():
    # 명령행 인수 처리
    if len(sys.argv) > 1:
        # 파일 목록이 명령행에서 제공된 경우
        log_files = sys.argv[1:]
    else:
        # 인수가 없으면 현재 디렉토리의 모든 .log 파일
        log_files = glob.glob("*.log")

    if not log_files:
        print("분석할 로그 파일이 없습니다.")
        return

    print(f"{len(log_files)}개 로그 파일 분석 시작...")

    # 로그 파일 분석
    results_df = analyze_bot_requests(log_files)

    # 결과 출력
    print("\n분석 결과:")
    print(results_df)

    # 결과 저장
    save_results(results_df, "csv")  # CSV 형식으로 저장
    save_results(results_df, "xlsx")  # Excel 형식으로도 저장 시도


if __name__ == "__main__":
    main()
