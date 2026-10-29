# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
from genlayer import *


UBA_API = "https://luftdaten.umweltbundesamt.de/api-proxy/airquality/json"
SUPPORTED_COMPONENTS = (1, 3, 4, 5, 9)


def valid_station_code(code: str) -> bool:
    if len(code) != 7 or not code.startswith("DE"):
        return False
    if code[2] < "A" or code[2] > "Z":
        return False
    if code[3] < "A" or code[3] > "Z":
        return False
    return code[4:7].isdigit()


def valid_date(value: str) -> bool:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    if not (value[:4] + value[5:7] + value[8:10]).isdigit():
        return False

    year = int(value[:4])
    month = int(value[5:7])
    day = int(value[8:10])

    if month < 1 or month > 12 or day < 1:
        return False

    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        days_in_month[1] = 29

    return day <= days_in_month[month - 1]


def value_to_milli(value) -> int:
    text = str(value).strip()
    if not text or text.startswith("-"):
        raise ValueError("Invalid pollutant reading")

    parts = text.split(".")
    whole = int(parts[0])
    fraction = ""

    if len(parts) > 1:
        fraction = (parts[1] + "000")[:3]

    return whole * 1000 + (int(fraction) if fraction else 0)


def summarize_response(
    payload: dict,
    local_date: str,
    time_from: int,
    time_to: int,
    component_id: int,
    threshold_milli: int,
) -> dict:
    request = payload.get("request", {})
    station_id = str(request.get("station", ""))
    station_rows = payload.get("data", {}).get(station_id, {})

    expected_hours = time_to - time_from + 1
    observed_hours = 0
    breach_hours = 0

    for slot in range(time_from, time_to + 1):
        start_hour = slot - 1
        hour_text = ("0" + str(start_hour))[-2:]
        timestamp = local_date + " " + hour_text + ":00:00"
        row = station_rows.get(timestamp)

        if not isinstance(row, list):
            continue

        reading = None
        for item in row[3:]:
            if isinstance(item, list) and len(item) >= 2:
                if int(item[0]) == component_id:
                    reading = item[1]
                    break

        if reading is None:
            continue

        observed_hours += 1
        if value_to_milli(reading) > threshold_milli:
            breach_hours += 1

    if observed_hours < expected_hours:
        outcome = "INCOMPLETE"
    elif breach_hours > 0:
        outcome = "BREACH"
    else:
        outcome = "WITHIN_LIMIT"

    return {
        "outcome": outcome,
        "expected_hours": expected_hours,
        "observed_hours": observed_hours,
        "breach_hours": breach_hours,
    }


class AirShiftWitness(gl.Contract):
    next_id: u256
    records: TreeMap[str, str]

    def __init__(self):
        self.next_id = u256(1)
        self.records = TreeMap()

    @gl.public.write
    def check_window(
        self,
        station_code: str,
        local_date: str,
        time_from: u256,
        time_to: u256,
        component_id: u256,
        threshold_milli: u256,
    ) -> str:
        if not valid_station_code(station_code):
            raise ValueError("Use an official UBA station code such as DEHE041")
        if not valid_date(local_date):
            raise ValueError("Date must use YYYY-MM-DD")

        start = int(time_from)
        end = int(time_to)
        component = int(component_id)
        threshold = int(threshold_milli)

        if start < 1 or end > 24 or start > end:
            raise ValueError("UBA time slots must satisfy 1 <= from <= to <= 24")
        if component not in SUPPORTED_COMPONENTS:
            raise ValueError("Unsupported pollutant component")

        url = (
            UBA_API
            + "?date_from=" + local_date
            + "&date_to=" + local_date
            + "&time_from=" + str(start)
            + "&time_to=" + str(end)
            + "&station=" + station_code
            + "&lang=en"
        )

        def leader_fn() -> str:
            response = gl.nondet.web.get(url)
            if response.status != 200:
                raise ValueError("UBA API request failed")

            payload = json.loads(response.body.decode("utf-8"))
            result = summarize_response(
                payload,
                local_date,
                start,
                end,
                component,
                threshold,
            )
            return json.dumps(result, sort_keys=True)

        def validator_fn(leader_result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False

            try:
                proposed = json.loads(leader_result.calldata)
                checked = json.loads(leader_fn())
            except Exception:
                return False

            return (
                proposed.get("outcome") == checked.get("outcome")
                and proposed.get("expected_hours") == checked.get("expected_hours")
                and proposed.get("observed_hours") == checked.get("observed_hours")
                and proposed.get("breach_hours") == checked.get("breach_hours")
            )

        result = json.loads(gl.vm.run_nondet_unsafe(leader_fn, validator_fn))

        record_id = str(int(self.next_id))
        record = {
            "id": record_id,
            "station_code": station_code,
            "local_date": local_date,
            "time_from": start,
            "time_to": end,
            "component_id": component,
            "threshold_milli": threshold,
            "assessment": result,
        }

        self.records[record_id] = json.dumps(record, sort_keys=True)
        self.next_id = u256(int(self.next_id) + 1)
        return record_id

    @gl.public.view
    def get_record(self, record_id: str) -> str:
        raw = self.records.get(record_id)
        if not raw:
            return "{}"
        return raw
