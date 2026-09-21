"""Synchronize repetitive utility-meter form translations (development helper)."""
import json
from pathlib import Path

FIELDS = {
    "cycle": ("Reset cycle", "초기화 주기", "Changing the cycle or dates retains the running total and schedules future resets from the edit onward. Use the reset action to start at zero.", "주기나 날짜를 바꾸면 현재 합계를 유지하고 변경 시점 이후의 초기화 일정부터 적용합니다. 0부터 시작하려면 초기화 Action을 실행하세요."),
    "start": ("Start date/time", "시작 날짜·시간", "ISO date/time, e.g. 2026-01-15T00:00:00. Local HA time unless an offset is supplied. Day 31 clamps to month end.", "예: 2026-01-15T00:00:00. 시간대가 없으면 HA 현지 시간입니다. 31일은 짧은 달의 말일에 맞춥니다."),
    "days": ("Repeat every N days", "N일마다 반복", "Calendar days from the start date; used for the N-day cycle.", "시작일로부터 반복할 달력 일수입니다. N일 주기에 사용합니다."),
    "offset": ("Offset (minutes)", "시작 오프셋(분)", "Shift the calendar anchor by 0–40319 minutes. Ignored for cron.", "달력 기준 시점을 0~40319분 이동합니다. cron에는 적용하지 않습니다."),
    "cron": ("Cron schedule", "Cron 일정", "Five-field cron in Home Assistant local time; used only for cron cycle.", "HA 현지 시간 기준 5필드 cron입니다. cron 주기에만 적용합니다."),
    "delta_values": ("Source reports increments", "소스가 증분 보고", "Add each new source event as consumption rather than subtracting cumulative readings.", "누적값의 차이 대신 새 소스 이벤트의 값을 사용량에 더합니다."),
    "net_consumption": ("Allow net consumption", "순사용량 허용", "Allow negative deltas for import/export meters; publishes total statistics.", "수입·수출 계량기의 음수 증분을 허용하며 total 통계를 게시합니다."),
    "periodically_resetting": ("Source periodically resets", "소스 주기적 초기화", "Discard the baseline across invalid source states. Disable for a lifetime counter to bridge outages.", "소스 오류 구간의 기준값을 폐기합니다. 평생 누적 미터는 끄면 단절 전후 차이를 반영합니다."),
    "always_available": ("Keep total available", "누적값 가용 유지", "Retain the accumulated total when the source is unavailable.", "소스가 사용 불가여도 누적 합계를 표시합니다."),
    "tariff_entity": ("Tariff selector entity", "요율 선택 엔티티", "Optional select/input_select entity; create one meter per tariff on this Device.", "선택적 select/input_select 엔티티입니다. 요율마다 이 Device에 미터를 하나씩 만드세요."),
    "tariff": ("Collecting tariff", "집계 요율", "Collect only while the tariff selector matches this exact option. Automations can switch the selector by time.", "선택기의 옵션과 이 값이 일치할 때만 집계합니다. 자동화에서 시간대별로 선택기를 전환할 수 있습니다."),
    "rate": ("Unit price", "단위 단가", "Price per source unit, also above the final tier. Editing prices recalculates the entire current-period estimate, not historical price segments.", "소스 단위당 가격이며 마지막 누진 구간 초과분에도 적용합니다. 단가 변경 시 현재 기간 전체 예상요금을 다시 계산하며 과거 단가 구간을 분리하지 않습니다."),
    "base_charge": ("Period base charge", "기간 기본요금", "Fixed charge per period. For multiple tariff meters, charge the base on one meter only.", "기간당 고정 요금입니다. 요율별 미터가 여러 개면 하나에만 기본요금을 설정하세요."),
    "currency": ("Currency", "통화", "Three-letter currency code such as KRW or USD.", "KRW, USD 등 세 글자 통화 코드입니다."),
    "tiers": ("Progressive tiers", "누진 구간", "List of objects with up_to and rate keys and increasing cumulative limits. Empty uses the unit price.", "누적 상한 up_to와 단가 rate 객체의 목록입니다. 상한은 오름차순이며 비우면 단일단가를 사용합니다."),
    "current_value": ("Correct current-period total", "현재 기간 사용량 보정", "Audited absolute total, applied once on save with a new source baseline. Leave blank to retain the total, including when changing the schedule. If offline, the first recovered reading becomes the baseline.", "검침한 현재 기간 절대 합계를 저장 시 한 번 적용하고 소스 기준값도 갱신합니다. 일정만 바꾸고 합계를 유지하려면 비우세요. 소스 단절 시 복구 후 첫 측정값을 기준으로 삼습니다."),
}

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "custom_components/virtual_layer/translations"
    for language in ("en", "ko"):
        path = root / (language + ".json")
        catalog = json.loads(path.read_text())
        for group, step in (("config", "entity"), ("options", "entity"), ("options", "edit_entity")):
            section = catalog[group]["step"][step]["sections"]["domain_settings"]
            for key, values in FIELDS.items():
                section["data"]["utility_meter_" + key] = values[language == "ko"]
                section["data_description"]["utility_meter_" + key] = values[2 + (language == "ko")]
        choices = catalog["selector"]["utility_meter_cycle"]["options"]
        for key, en, ko in (("none", "No automatic reset", "자동 초기화 없음"), ("quarter-hourly", "Every 15 minutes", "15분마다"), ("hourly", "Hourly", "매시간"), ("bimonthly", "Every two months", "두 달마다"), ("days", "Every N days", "N일마다"), ("cron", "Cron schedule", "Cron 일정")):
            choices[key] = ko if language == "ko" else en
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
