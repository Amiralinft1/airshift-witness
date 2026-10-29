# { "Depends": "py-genlayer:test" }

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


def summarize_uba_response(
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
    def propose_window(
        self,
        counterparty: str,
        station_code: str,
        local_date: str,
        time_from: u256,
        time_to: u256,
        component_id: u256,
        threshold_milli: u256,
    ) -> int:
        if not valid_station_code(station_code):
            raise ValueError("Use an official UBA station code such as DEHE041")
        if not valid_date(local_date):
            raise ValueError("Date must use YYYY-MM-DD")
        if int(time_from) < 1 or int(time_to) > 24:
            raise ValueError("UBA time slots must be between 1 and 24")
        if int(time_from) > int(time_to):
            raise ValueError("time_from must not exceed time_to")
        if int(component_id) not in SUPPORTED_COMPONENTS:
            raise ValueError("Unsupported pollutant component")

        partner = Address(counterparty)
        proposer = gl.message.sender_address
        if partner.as_hex.lower() == proposer.as_hex.lower():
            raise ValueError("Counterparty must be a different address")

        window_id = int(self.next_id)
        record = {
            "id": window_id,
            "proposer": proposer.as_hex,
            "counterparty": partner.as_hex,
            "station_code": station_code,
            "local_date": local_date,
            "time_from": int(time_from),
            "time_to": int(time_to),
            "component_id": int(component_id),
            "threshold_milli": int(threshold_milli),
            "status": "PROPOSED",
            "assessment": "",
        }

        key = str(window_id)
        self.records[key] = json.dumps(record, sort_keys=True)
        self.next_id = u256(window_id + 1)
        return window_id

    @gl.public.write
    def accept_window(self, window_id: u256) -> str:
        key = str(int(window_id))
        raw = self.records.get(key)
        if not raw:
            raise ValueError("Window not found")

        record = json.loads(raw)
        if gl.message.sender_address.as_hex.lower() != record["counterparty"].lower():
            raise ValueError("Only the named counterparty can accept")
        if record["status"] != "PROPOSED":
            raise ValueError("Window is not awaiting acceptance")

        record["status"] = "ACCEPTED"
        saved = json.dumps(record, sort_keys=True)
        self.records[key] = saved
        return saved

    @gl.public.write
    def assess_window(self, window_id: u256) -> str:
        key = str(int(window_id))
        raw = self.records.get(key)
        if not raw:
            raise ValueError("Window not found")

        record = json.loads(raw)
        if record["status"] == "PROPOSED":
            raise ValueError("Counterparty must accept the policy first")
        if record["status"] in ("RESOLVED_BREACH", "RESOLVED_WITHIN_LIMIT"):
            raise ValueError("Window already has a final assessment")

        url = (
            UBA_API
            + "?date_from=" + record["local_date"]
            + "&date_to=" + record["local_date"]
            + "&time_from=" + str(record["time_from"])
            + "&time_to=" + str(record["time_to"])
            + "&station=" + record["station_code"]
            + "&lang=en"
        )

        def leader_fn() -> str:
            response = gl.nondet.web.get(url)
            if response.status_code != 200:
                raise ValueError("UBA API request failed")

            payload = json.loads(response.body.decode("utf-8"))
            result = summarize_uba_response(
                payload,
                record["local_date"],
                int(record["time_from"]),
                int(record["time_to"]),
                int(record["component_id"]),
                int(record["threshold_milli"]),
            )
            return json.dumps(result, sort_keys=True)

        def validator_fn(leader_result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False

            try:
                proposed = json.loads(leader_result.calldata)
                independently_checked = json.loads(leader_fn())
            except Exception:
                return False

            return (
                proposed.get("outcome") == independently_checked.get("outcome")
                and proposed.get("expected_hours") == independently_checked.get("expected_hours")
                and proposed.get("observed_hours") == independently_checked.get("observed_hours")
                and proposed.get("breach_hours") == independently_checked.get("breach_hours")
            )

        result = json.loads(gl.vm.run_nondet_unsafe(leader_fn, validator_fn))
        record["assessment"] = json.dumps(result, sort_keys=True)

        if result["outcome"] == "INCOMPLETE":
            record["status"] = "PENDING_DATA"
        elif result["outcome"] == "BREACH":
            record["status"] = "RESOLVED_BREACH"
        else:
            record["status"] = "RESOLVED_WITHIN_LIMIT"

        self.records[key] = json.dumps(record, sort_keys=True)
        return record["assessment"]

    @gl.public.view
    def get_window(self, window_id: u256) -> str:
        raw = self.records.get(str(int(window_id)))
        if not raw:
            return "{}"
        return raw
