"""수업용 작은 버그: 고정 비용과 참가자별 비용의 계산을 점검하세요."""


def total_cost(people: int, per_person: int, venue: int) -> int:
    if min(people, per_person, venue) < 0:
        raise ValueError("비용과 인원은 음수일 수 없습니다.")
    return people * (per_person + venue)
