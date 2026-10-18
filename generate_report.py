#!/usr/bin/env python3
import argparse
import json
from datetime import datetime
from pathlib import Path

PROFILE = [
    ("GET /booking", "list", "list_requests", "list_duration", "list_errors", 50),
    ("GET /booking/:id", "detail", "detail_requests", "detail_duration", "detail_errors", 30),
    ("POST /booking", "create", "create_requests", "create_duration", "create_errors", 20),
]


def load_json(path):
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def value(data, name, field, fallback=None):
    if not data:
        return fallback
    return data.get("metrics", {}).get(name, {}).get("values", {}).get(field, fallback)


def number(n, places=2):
    if n is None:
        return "—"
    return f"{n:,.{places}f}".replace(",", " ")


def intstr(n):
    if n is None:
        return "—"
    return f"{round(n):,}".replace(",", " ")


def percent(n):
    return "—" if n is None else number(n * 100) + "%"


def change(before, after, unit="мс"):
    if before is None or after is None or before == 0:
        return "Недостаточно данных для сравнения."
    d = after - before
    direction = "увеличился" if d > 0 else "уменьшился" if d < 0 else "не изменился"
    return (f"{direction}: {number(before)} → {number(after)} {unit} "
            f"({d / before * 100:+.1f}%).")


def success(data, name, condition):
    measured = value(data, *name)
    if measured is None:
        return "не измерено"
    return "выполнен" if condition(measured) else "нарушен"


def get_worst(data):
    candidates = [(value(data, trend, "p(95)"), endpoint, title)
                  for title, endpoint, _, trend, _, _ in PROFILE]
    candidates = [c for c in candidates if c[0] is not None]
    return max(candidates) if candidates else None


def render(root, baseline, increased):
    measured = baseline is not None and increased is not None
    recorded = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Отчёт: нагрузочное тестирование REST API Restful-Booker",
        "",
        "## 1. Цель и окружение",
        "",
        "Цель — оценить поведение трёх операций REST API при базовой и повышенной нагрузке, "
        "сравнить задержки, пропускную способность и ошибки.",
        "",
        "- Тестируемый сервис: [Restful-Booker](https://github.com/mwinteringham/restful-booker), "
        "исходный код из предоставленного архива.",
        "- Развёртывание: Docker Compose, сервис на порту `3001`.",
        "- Генератор запросов: **k6** в отдельном Docker-контейнере с общей сетевой средой API.",
        "- Целевой адрес для k6: `http://localhost:3001` (внутри общей сетевой среды контейнеров).",
        "- Состояние данных: при каждом запуске контейнер API создаётся заново; "
        "сидовые бронирования восстанавливаются, накопленные POST-запросы обнуляются.",
        "- Отчёт сформирован: " + recorded + ".",
        "",
        "**Статус:** " + ("оба запуска завершены, метрики прочитаны из JSON k6."
                                if measured else "запуски в текущей среде не выполнены; числовые результаты отсутствуют."),
        "",
        "## 2. Профиль нагрузки (определён до тестирования)",
        "",
        "| Операция | Метод и endpoint | Доля запросов | Назначение |",
        "|---|---|---:|---|",
        "| Список бронирований | `GET /booking` | 50% | Частое чтение списка |",
        "| Конкретное бронирование | `GET /booking/:id` | 30% | Чтение карточки по существующему ID |",
        "| Создание бронирования | `POST /booking` | 20% | Изменение данных (запись) |",
        "",
        "Профиль ориентирован на чтение (80%) с умеренной долей создания данных (20%). "
        "ID для чтения выбираются из начального набора существующих бронирований, "
        "чтобы не создавать искусственные 404. Фактические доли могут немного отличаться "
        "от заданных из-за случайного выбора операции.",
        "",
        "| Параметр | Запуск 1: базовый | Запуск 2: повышенный |",
        "|---|---|---|",
        "| Тип модели | Закрытая, ramping VUs | Закрытая, ramping VUs |",
        "| Одновременные виртуальные пользователи | 0 → 10 → 10 → 0 | 0 → 40 → 40 → 0 |",
        "| Ramp-up | 0–30 с | 0–30 с |",
        "| Plateau | 30–120 с (90 с) | 30–120 с (90 с) |",
        "| Ramp-down | 120–150 с (30 с) | 120–150 с (30 с) |",
        "| Общая запланированная длительность | 150 с | 150 с |",
        "| Пауза между операциями | 0,2–0,5 с | 0,2–0,5 с |",
        "| Целевая интенсивность | до 10 VUs | до 40 VUs |",
        "",
        "В closed-loop модели интенсивность выражается числом активных пользователей, "
        "а не фиксированным RPS. Фактический RPS зависит от времени ответа и пауз. "
        "При увеличении пользователей в 4 раза можно оценить масштабируемость приложения.",
        "",
        "## 3. Критерии успешности (заданы до получения результатов)",
        "",
        "1. `http_req_duration p95 < 500 мс` — 95% запросов должны выполняться быстрее 500 мс.",
        "2. `http_req_failed < 1%` — доля HTTP-ошибок должна оставаться менее 1%.",
        "3. `scenario_errors < 1%` — доля ответов, не прошедших проверку статуса и структуры, должна быть менее 1%.",
        "",
        "Критерии применяются к **каждому** запуску. Для API проверяются не только коды "
        "HTTP, но и структура JSON-ответа. Времена — измерения k6, миллисекунды.",
        "",
        "## 4. Результаты двух запусков",
        "",
        "| Метрика | Базовая нагрузка (10 VUs) | Повышенная нагрузка (40 VUs) |",
        "|---|---:|---:|",
    ]
    rows = [
        ("Всего HTTP-запросов", "http_reqs", "count", intstr),
        ("Throughput, RPS", "http_reqs", "rate", number),
        ("Среднее время ответа, мс", "http_req_duration", "avg", number),
        ("p95, мс", "http_req_duration", "p(95)", number),
        ("p99, мс", "http_req_duration", "p(99)", number),
        ("HTTP-ошибки, %", "http_req_failed", "rate", percent),
        ("Ошибки сценариев, %", "scenario_errors", "rate", percent),
    ]
    for title, metric, field, fmt in rows:
        lines.append(f"| {title} | {fmt(value(baseline, metric, field))} | {fmt(value(increased, metric, field))} |")
    lines.extend(["", "### Проверка критериев", "",
                  "| Критерий | Запуск 1 | Запуск 2 |",
                  "|---|---|---|"])
    thresholds = [
        ("p95 < 500 мс", ("http_req_duration", "p(95)"), lambda n: n < 500),
        ("HTTP-ошибки < 1%", ("http_req_failed", "rate"), lambda n: n < 0.01),
        ("Ошибки сценариев < 1%", ("scenario_errors", "rate"), lambda n: n < 0.01),
    ]
    for title, metric, rule in thresholds:
        lines.append(f"| {title} | {success(baseline, metric, rule)} | {success(increased, metric, rule)} |")
    lines.extend(["", "### Метрики по операциям", "",
                  "| Запуск | Endpoint | Запросов | Факт. доля | Среднее, мс | p95, мс | p99, мс | Ошибки сценария |",
                  "|---|---|---:|---:|---:|---:|---:|---:|"])
    for label, obj in [("10 VUs", baseline), ("40 VUs", increased)]:
        total = sum(value(obj, metric, "count", 0) for _, _, metric, _, _, _ in PROFILE)
        for title, _, counter, trend, error, _ in PROFILE:
            count = value(obj, counter, "count")
            share = None if count is None or total == 0 else count / total
            lines.append(f"| {label} | `{title}` | {intstr(count)} | {percent(share)} | "
                         f"{number(value(obj, trend, 'avg'))} | {number(value(obj, trend, 'p(95)'))} | "
                         f"{number(value(obj, trend, 'p(99)'))} | {percent(value(obj, error, 'rate'))} |")
    lines.extend(["", "*Счётчики операций не включают служебные запросы `/ping` и "
                  "`GET /booking` в фазе setup; общая метрика HTTP-запросов включает их.*", "",
                  "## 5. Анализ результатов и ответы на вопросы", ""])
    if not measured:
        lines.extend([
            "1. **Как изменилось среднее время ответа?** Невозможно определить без результатов обоих запусков.",
            "2. **Как изменились p95 и p99?** Не измерены.",
            "3. **Появились ли ошибки?** Неизвестно без вывода k6.",
            "4. **Выполнены ли критерии?** Пока не проверены.",
            "5. **Есть ли деградация?** Пока нельзя установить.",
            "6. **Самый медленный endpoint?** Нельзя определить без p95 по каждой операции.",
            "7. **Можно ли установить причину деградации?** Нет, одних клиентских метрик недостаточно.",
        ])
    else:
        avg_a, avg_b = (value(x, "http_req_duration", "avg") for x in (baseline, increased))
        p95_a, p95_b = (value(x, "http_req_duration", "p(95)") for x in (baseline, increased))
        p99_a, p99_b = (value(x, "http_req_duration", "p(99)") for x in (baseline, increased))
        http_a, http_b = (value(x, "http_req_failed", "rate", 0) for x in (baseline, increased))
        scr_a, scr_b = (value(x, "scenario_errors", "rate", 0) for x in (baseline, increased))
        rps_a, rps_b = (value(x, "http_reqs", "rate") for x in (baseline, increased))
        worst = get_worst(increased)
        violated = [(run, criterion) for run, obj in [("10 VUs", baseline), ("40 VUs", increased)]
                    for criterion, metric, rule in thresholds
                    if success(obj, metric, rule) == "нарушен"]
        degraded = ((p95_a is not None and p95_b is not None and p95_b > p95_a * 1.2)
                    or http_b > http_a or scr_b > scr_a)
        lines.extend([
            "1. **Как изменилось среднее время ответа?** " + change(avg_a, avg_b),
            "2. **Как изменились p95 и p99?** p95 " + change(p95_a, p95_b) + " p99 " + change(p99_a, p99_b),
            "3. **Появились ли ошибки?** HTTP: " + f"{percent(http_a)} → {percent(http_b)}; "
              + f"ошибки проверок сценария: {percent(scr_a)} → {percent(scr_b)}.",
            "4. **Выполнены ли критерии?** " + (
                "Не все: " + "; ".join(f"{run}: {criterion}" for run, criterion in violated) + "."
                if violated else "Да, все три критерия выполнены для обоих запусков."),
            "5. **Есть ли признаки деградации?** " + (
                "Есть: при повышении нагрузки вырос p95 более чем на 20% и/или доля ошибок. "
                if degraded else "Явных признаков по принятому ориентиру (>20% рост p95 или рост доли ошибок) нет. ")
              + f"Пропускная способность: {number(rps_a)} → {number(rps_b)} RPS. "
              + "Оценка причин требует серверных наблюдений.",
            "6. **Какой endpoint показал наихудший результат?** " + (
                f"`{worst[2]}` по p95 при 40 VUs: {number(worst[0])} мс."
                if worst else "Недостаточно данных по отдельным операциям."),
            "7. **Можно ли установить причину деградации?** Нет. "
              "Клиентские показатели подтверждают лишь характер симптомов, но не дают доказательства узкого места.",
        ])
    lines.extend([
        "8. **Какие серверные метрики нужны для поиска bottleneck?** "
        "CPU и throttling контейнера, RAM/RSS, heap и частота/длительность GC, "
        "задержка event loop Node.js, длительность обработки каждого маршрута, "
        "размер коллекции LokiJS, время запросов к хранилищу, размер HTTP-ответов, "
        "активные соединения, сетевой I/O, число HTTP 5xx/4xx и логи ошибок. "
        "Также важна загрузка самого генератора k6.",
        "9. **Общий результат.** Сопоставление нагрузок 10 и 40 VUs на одинаковом профиле "
        "позволяет проверить рост задержек и ошибок, но без серверной телеметрии невозможно "
        "уверенно назвать причину изменений.",
        "",
        "### Гипотеза для дополнительной проверки",
        "",
        "Исходный код использует **LokiJS в памяти процесса Node.js**. Каждое создание бронирования "
        "увеличивает коллекцию, а `GET /booking` формирует список существующих ID. "
        "Возможная гипотеза: рост коллекции и размера ответа повышает стоимость получения списка. "
        "Для проверки нужно сопоставить p95 `GET /booking` с числом записей, CPU, "
        "задержкой event loop, временем обработки маршрута и размером возвращённого JSON. "
        "Это гипотеза, а не установленная причина.",
        "",
        "## 6. Итоговый вывод",
        "",
    ])
    if not measured:
        lines.append("Запланированы два теста по 150 секунд при **10 и 40 виртуальных пользователях**. "
                     "В этой среде запуски не состоялись, поэтому изменение скорости, нарушения критериев "
                     "и наиболее чувствительный endpoint пока **не установлены**. После запуска `run_all.sh` "
                     "в этом разделе автоматически появится вывод на основе фактических метрик. "
                     "Для анализа возможного узкого места понадобятся CPU, RAM, GC, event loop и "
                     "метрики обработки API/коллекции LokiJS.")
    else:
        before, after = value(baseline, "http_req_duration", "p(95)"), value(increased, "http_req_duration", "p(95)")
        worst = get_worst(increased)
        violated = [(run, label) for run, obj in [("10 VUs", baseline), ("40 VUs", increased)]
                    for label, metric, rule in thresholds if success(obj, metric, rule) == "нарушен"]
        lines.append(
            "Выполнены два теста по **150 секунд**, максимум **10 и 40 одновременных пользователей** "
            "с распределением операций **50%/30%/20%**. "
            f"При повышенной нагрузке p95 изменился с {number(before)} до {number(after)} мс, "
            f"RPS — с {number(value(baseline, 'http_reqs', 'rate'))} до "
            f"{number(value(increased, 'http_reqs', 'rate'))}. "
            + ("Нарушены критерии: " + "; ".join(f"{run} — {label}" for run, label in violated) + ". "
               if violated else "Все критерии успешности выполнены в обоих запусках. ")
            + (f"Наиболее чувствительный по p95 при 40 VUs — `{worst[2]}` "
               f"({number(worst[0])} мс). " if worst else "Чувствительный endpoint не определён. ")
            + "Для проверки причин требуются серверные метрики CPU, памяти, GC, "
              "event loop, длительности обработчиков и состояния коллекции LokiJS."
        )
    lines.extend(["", "## 7. Приложения и воспроизводимость", "",
                  "- `load_test.js` — сценарий k6 с тремя операциями, паузами, проверками и thresholds.",
                  "- `LOAD_PROFILE.md` — отдельно оформленный профиль нагрузки.",
                  "- `run_all.sh` — запуск двух тестов с независимым пересозданием API.",
                  "- `generate_report.py` — сбор результатов и Markdown-отчёта.",
                  "- `results/baseline-summary.json`, `results/increased-summary.json` — исходные метрики k6 (после запуска).",
                  "- `results/*-console.log` — консольный журнал (после запуска).",
                  "- `results/environment.txt`, `results/*-preflight.txt` — данные окружения и предварительная проверка API.",
                  "",
                  "Подробная инструкция по запуску: `README.md`.", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    root = args.root.resolve()
    baseline = load_json(root / "results" / "baseline-summary.json")
    increased = load_json(root / "results" / "increased-summary.json")
    target = root / "REPORT.md"
    target.write_text(render(root, baseline, increased), encoding="utf-8")
    print(f"Saved: {target}")


if __name__ == "__main__":
    main()
