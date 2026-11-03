# PagedAttention: управление памятью KV-кэша на CPU

Учебный проект НИС, **команда 14**, задание **P23**.

- [Карточка команды](https://distsys.ru/osukhoroslov/nis2/src/branch/main/projects/team-14.md).
- [Назначенное задание](https://distsys.ru/osukhoroslov/nis2/src/branch/main/project-tasks/p23-pagedattention.md).
- [Статья: Efficient Memory Management for Large Language Model Serving with PagedAttention, SOSP 2023](https://arxiv.org/abs/2309.06180).
- [Требования первой точки](https://distsys.ru/osukhoroslov/nis2/src/branch/main/project-checkpoints/01-project-plan.md).

Сравниваем непрерывное резервирование максимальной длины запроса со страничным выделением по мере роста. Выбран путь самостоятельной реализации на CPU над реально выделенным `bytearray`. Первая точка содержит прототип аллокаторов и технические проверки. Динамический обработчик и полное сравнение запланированы на следующие этапы.

## Участники

Полные ФИО трёх участников и согласованный личный вклад пока не предоставлены. Карточка курса возвращает скрытые имена. Перед сдачей команда должна заполнить имена здесь и подтвердить записи в [карточке первой точки](nis/checkpoints/01.md#ответственность-участников). Авторство и согласование ответственности не выводятся из автоматически подготовленных файлов.

## Быстрый запуск

Нужен Python **3.9+**, внешних Python-зависимостей и GPU нет.

```bash
git clone https://github.com/Kera-0/GroupProject.git
cd GroupProject
git switch nis/checkpoint-1
python3 -m unittest discover -s tests -v
python3 scripts/checkpoint.py --output /tmp/team14-checkpoint-1
```

Последняя команда повторяет тесты и шесть аналитических проверок, сохраняет результаты, окружение и SHA-256 в указанный каталог. Ожидаемый итог: `PASS: unit tests and 6 analytical checks`. Времена операций и дата нового запуска отличаются; детерминированные значения памяти совпадают. Запуск без `--output` обновляет сохранённые свидетельства в `artifacts/checkpoint-1/`.

## Навигация

| Материал | Содержание |
|---|---|
| [nis/project.md](nis/project.md) | Обязательные результаты, технический путь, сценарии, ограничения |
| [nis/experiment-plan.md](nis/experiment-plan.md) | Baseline, нагрузки, серии, метрики, обработка |
| [nis/checkpoints/01.md](nis/checkpoints/01.md) | Итоги первой точки, риски и ответственность |
| [nis/results.md](nis/results.md) | Протокол воспроизведения и описание данных |
| [nis/status.md](nis/status.md) | Готовность и оставшиеся действия |
| [kv_cache/allocators.py](kv_cache/allocators.py) | Взаимозаменяемые аллокаторы и инварианты |
| [tests/test_allocators.py](tests/test_allocators.py) | Аналитические и случайные проверки |
| [configs/checkpoint-1.json](configs/checkpoint-1.json) | Конфигурации технических примеров |
| [scripts/checkpoint.py](scripts/checkpoint.py) | Запуск проверок и сохранение свидетельств |
| [artifacts/checkpoint-1/summary.md](artifacts/checkpoint-1/summary.md) | Полученные числа и ссылки на исходные измерения |

Для сдачи нужна публикация коммита ветки `nis/checkpoint-1` и PR в `main`, содержащий полный SHA и ссылки на документы. PR до проверки преподавателем не объединяется. Репозиторий также регистрируется через [форму курса](https://forms.gle/DRvt2BEWdkWNDSG76). Подробности и оставшиеся организационные действия — в [nis/submission.md](nis/submission.md).
