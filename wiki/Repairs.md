# Explicit repairs and comparison

The Resolution column has **Repair**, opening a comparison dialog. Clicking it
explicitly schedules a durable `repair` job; GET never generates a candidate.
Originals are immutable. Candidates remain outside the catalogue until the user
chooses one. The repair queue has its own pause/retry and lifetime worker lock.

`font_repairs.py` first asks OTS to serialize the original into a private output.
If OTS refuses, a separate attempt uses fontTools resserialization and the existing
tolerant cmap restoration, then OTS serialization. It never arbitrarily deletes
tables to force acceptance. This user-requested stage is an exception to the
ordinary original gate: parsing occurs in an isolated repair worker with the
existing heavy-font admission lock. FontTools work has no hard time/memory sandbox.

The candidate must pass another full OTS check. Failed partial output is not
downloadable or selectable. The UI shows native/repair failure text when there is
no usable candidate; it still permits explicit admission of the original.

Comparison covers container size, face count, family/style, glyph/Unicode counts,
tables and OpenType features, plus removed/added codepoints and removed tables or
features. It does not prove equivalent outlines or shaping. Original comparison
may be unavailable when parsing fails, which the dialog states explicitly.

Choosing Original records admission with a reason and resumes normal analysis.
Choosing Candidate copies the checked bytes to intake, assigns a new SHA-256 and
retains the original source and original-to-candidate lineage. Standard queues
perform analysis and browser preview; publication is not performed in the HTTP
request. Existing catalogue records are not silently deleted or replaced.

`repair_candidates` stores ready comparison artifacts and reports;
`repair_choices` stores original hash, chosen hash, source, choice and timestamp.
Archive backups include `repairs`; restore relocates candidate paths.
`test_font_repairs.py` checks real OTS output, byte preservation, both choices,
invalid sources, refused candidates and portable backups.

## Массовая проверка восстановления

Кнопка «Попробовать восстановить все отклонённые» ставит в отдельную последовательную очередь все физические файлы со статусом OTS `rejected`, для которых ещё не было попытки восстановления. Повторное нажатие не повторяет готовые или неудачные попытки. Отдельную неудачную попытку можно повторить через «Исправление». Результаты показаны в столбце «Разрешение» каталога проверок после обновления таблицы. Автоматической публикации и обхода OTS нет: пользователь сравнивает и выбирает версию отдельно.

## Автоматическое восстановление при приёме

Поток: сохранение неизменяемого оригинала → OTS → при отказе очередь восстановления → OTS обработанной копии → проверка сохранности → обычный приём копии, её допуск OTS, анализ и предпросмотр. Если оригинал прошёл OTS, восстановление не запускается. При технической ошибке, тайм-ауте или неподдерживаемом контейнере файл остаётся проблемой проверки: автоматического обхода нет.

Автоматическое восстановление применяется к новым логическим экземплярам, ожидающим допуска (`held_origins`), при политике источника `block`. Результаты старого каталога и источники с политикой `warn`/`skip` не создают автоматические исправления. При повторном приёме физического файла с известным отказом OTS используется тот же механизм; задания и попытки не размножаются. `repair_auto_origins` хранит состояние по паре оригинал/источник: pending, published, review, skipped. Настройки очереди восстановления действуют и для автоматических заданий.

Автоматический допуск консервативен: исходные и исправленные начертания должны полностью читаться, совпадать по числу, именам, глифам, символам, таблицам и функциям. Сопоставляются контрольные суммы **всех** cmap-подтаблиц с вариационными селекторами и остальных таблиц. Для `name` сравниваются полные логические записи, допускаются перестановка и устранение точных дубликатов. В `head` игнорируются только checksumAdjustment и время последнего изменения. Изменение контуров, GDEF/GSUB/GPOS, состава таблиц или неоднозначное чтение требует ручного решения, даже при OTS Pass. Старые отчёты без таких контрольных данных не считаются доказательством сохранности. Это намеренно строгая политика, а не гарантия, что всякую копию, прошедшую OTS, можно публиковать автоматически.

Оригинал с ошибкой сохраняет свой статус OTS и остаётся удержанным. Проверенная копия получает собственный hash и проходит штатный приём; её связь с оригиналом записывается в `repair_choices` с choice=automatic и в repair_auto_origins. На странице копии есть отметка «Исправлен при импорте», ссылка на оригинал и отчёт. Ранее опубликованные экземпляры автоматически не заменяются.

Пока идёт восстановление, связанные проблемы новой блокируемой инстанции не показываются как карантин. Все проверки по-прежнему доступны в каталоге проверок, работа — в очереди «Восстановление». Неудачная попытка, неподтверждённая сохранность или исчерпание повторов после аварии возвращают проблему в карантин. Успешная автоматическая публикация отмечает старые связанные проблемы рассмотренными, не меняя результат OTS оригинала.

Работа переживает перезапуск через постоянную очередь. Публикация идемпотентна по hash и источнику: повтор после аварии не создаёт повторный экземпляр. Файл исправления сохраняется отдельно; восстановление резервной копии переносит его путь вместе с базой, где хранятся состояния и происхождение.

Проверка: `test_auto_repairs.py` создаёт реальный OTF с некорректным searchRange cmap, проверяет отказ OTS, автоматическую пересборку, сохранность оригинала, штатный анализ исправленной копии и отметку происхождения. Отдельно проверяет удержание частично читаемой cmap и неудачного восстановления в карантине. `test_font_repairs.py` проверяет ручные варианты, отказ кандидата и резервное восстановление.
