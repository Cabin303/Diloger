# Universal Dialogue Trainer — техническое задание для агента

Версия: 1.0  
Статус: implementation brief для начала разработки  
Проект: Diloger  
Целевая платформа: macOS на Apple Silicon  
Рабочая папка: корень репозитория

## 0. Назначение документа

Этот документ является рабочим ТЗ для агента, который будет реализовывать MVP Universal Dialogue Trainer. Агент обязан прочитать его вместе с:

1. `AGENTS.md`
2. `docs/concept.md`

При противоречии:

1. прямые указания пользователя имеют высший приоритет;
2. AGENTS.md задаёт постоянные ограничения проекта;
3. этот документ задаёт конкретный план и критерии реализации;
4. concept.md содержит архитектурный контекст и обоснование решений.

Если агент обнаружит настоящее противоречие, он не должен молча выбирать случайный вариант. Он обязан зафиксировать противоречие, выбрать безопасное поведение, сообщить о нём и не расширять scope.

## 1. Результат, который нужно построить

Нужно создать локальное macOS-приложение для тренировки устных и письменных ответов на пользовательские английские реплики.

Главный сценарий:

    пользователь выбирает файл с репликами
            ↓
    приложение произносит реплику собеседника
            ↓
    пользователь отвечает вслух или печатает ответ
            ↓
    пользователь или таймер переводит сессию дальше
            ↓
    после сессии, только при наличии записи, запускается локальный Whisper

Приложение не является:

- AI tutor;
- chatbot;
- AI conversation partner;
- grammar checker;
- pronunciation evaluator;
- semantic answer evaluator;
- системой выставления оценок;
- системой определения намерения пользователя;
- системой определения конца речи.

Главный критерий продукта: приложение должно быстро и предсказуемо задавать следующую реплику и не мешать пользователю говорить.

## 2. Жёсткие ограничения

Агент не имеет права нарушать следующие ограничения ради удобства реализации:

1. Не использовать real-time STT.
2. Не запускать Whisper во время тренировки.
3. Не использовать Whisper или другой AI для определения конца ответа.
4. Не использовать VAD для автоматического перехода в MVP.
5. Не оценивать правильность, грамматику, произношение, беглость или смысл ответа.
6. Не генерировать новые реплики через LLM.
7. Не выбирать следующий вопрос по содержанию ответа.
8. Не отправлять пользовательское аудио, текст или контент в облако.
9. Не скачивать модели Whisper автоматически.
10. Не добавлять аккаунты, синхронизацию, онлайн-базу, подписки, gamification или социальные функции.
11. Не связывать TTS с Whisper.
12. Не связывать Content Repository с UI.
13. Не связывать Session Engine с конкретным TTS runtime.
14. Не делать запись обязательной.
15. Не хранить записи постоянно без явного выбора пользователя.
16. Не превращать MVP в редактор учебных материалов.
17. Не внедрять сложный Markdown-парсер без доказанной необходимости.
18. Не реализовывать мобильную или Windows-версию в рамках этого этапа.

Если добавляемая функция не нужна для основного цикла «задать реплику → дать время → перейти дальше», она должна быть отложена.

## 3. Технологический стек

### Обязательная рекомендация

- Python 3.11+;
- PySide6;
- Qt Quick/QML для визуального слоя;
- Qt Multimedia с macOS Core Audio backend для записи и воспроизведения;
- локальный macOS TTS provider;
- whisper-cli как локальный внешний процесс;
- JSON для конфигурации, сессии и event log;
- Markdown и JSON для экспорта.

PySide6 выбран вместо PyQt6 как основной binding из-за более удобной для этого проекта официальной Qt for Python/LGPL-oriented distribution path. Tauri и Electron не являются MVP-стеком: они добавляют web frontend, bridge и отдельные packaging/runtime concerns, которые не нужны Python-first локальному приложению.

### TTS

На целевой машине уже обнаружены:

- /Applications/KokoroVoice.app
- /Applications/kokoro-clipboard-tts.app

Команда macOS say показывает голоса:

- Kokoro Heart;
- Kokoro Michael.

Первый TTS provider должен использовать системный вызов или native API, позволяющий обращаться к установленным Kokoro-голосам. Прямое управление GUI Kokoro-приложений не является обязательным и не должно становиться архитектурной зависимостью.

TTS должен быть изолирован интерфейсом. Минимальный provider должен уметь:

- озвучить текст;
- сообщить о завершении;
- сообщить об ошибке;
- остановить текущую озвучку;
- вернуть список или проверить доступность голосов;
- установить голос;
- установить скорость.

Если Kokoro-голос недоступен, приложение может использовать выбранный локальный системный английский голос. Облачный TTS запрещён.

### Whisper

Использовать установленный локальный whisper-cli, если он найден.

Известные модели:

- `~/whisper/models/ggml-large-v3-turbo.bin`
- `~/whisper/models/ggml-large-v3.bin`

Путь к runtime и путь к модели должны находиться в конфигурации. Нельзя зашивать пользовательский путь в бизнес-логику.

Предварительно обнаружен whisper.cpp 1.9.1 с Metal, BLAS и Apple Silicon CPU backend. Это необходимо проверить техническим probe на реальном WAV-файле.

Модель large-v3-turbo является кандидатом для первого benchmark, но агент не должен автоматически объявлять её лучшей без измерения качества и времени.

## 4. Порядок работы агента

Агент обязан работать в следующем порядке.

### Шаг 1. Изучение контекста

До создания production-кода агент читает:

- AGENTS.md;
- docs/concept.md;
- этот файл;
- фактическое содержимое рабочей папки;
- состояние git, если git-репозиторий существует;
- доступность Python, virtual environment, PySide6, Qt tooling, whisper-cli, моделей и TTS.

Если в рабочей папке уже есть пользовательские изменения, агент не удаляет их и не перезаписывает без необходимости.

### Шаг 2. Технический Phase 0

Перед реализацией MVP агент проводит минимальные технические проверки:

1. Проверяет запрос разрешения на микрофон.
2. Записывает тестовый WAV в mono PCM 16 kHz или документирует, почему выбран другой стабильный формат.
3. Проверяет старт, завершение и остановку TTS.
4. Проверяет получение события завершения TTS.
5. Проверяет доступность Kokoro Heart и Kokoro Michael.
6. Проверяет whisper-cli на представительном пользовательском WAV.
7. Запускает benchmark обеих существующих моделей на одной и той же записи.
8. Фиксирует время, успешность, примерное потребление памяти и ошибки.
9. Проверяет, что временный аудиофайл можно удалить после успешной обработки.

Технические probes могут быть throwaway. Они не должны попадать в production target без отдельного обоснования.

### Шаг 3. Implementation plan

До большой реализации агент создаёт краткий план с:

- этапами;
- файлами;
- зависимостями;
- тестами;
- критериями готовности каждого этапа;
- решениями по обнаруженным рискам.

Если доступен отдельный writing-plans skill, его можно использовать после утверждения архитектурного документа. Если skill недоступен, план создаётся вручную в рабочем документе или в отчёте агента.

### Шаг 4. Реализация по вертикальным срезам

Рекомендуемый порядок:

1. domain models и parser;
2. Session Engine без реального аудио;
3. TTS adapter;
4. recording adapter;
5. Written Mode;
6. post-session Whisper adapter;
7. storage/export;
8. минимальный UI;
9. error states;
10. ручная проверка полного сценария.

Не начинать с визуальной полировки.

### Шаг 5. Проверка

После каждого вертикального среза агент запускает соответствующие unit/integration tests. Перед отчётом выполняет полный acceptance checklist из этого документа.

## 5. Использование субагентов

Субагенты разрешены, но не обязательны. Их задача — ускорить независимые исследования, а не размыть ответственность.

### Разрешённые роли

#### TTS/macOS probe agent

Проверяет:

- наличие Kokoro-системных голосов;
- способы запуска TTS;
- получение события завершения;
- rate control;
- stop/replay;
- fallback на системный голос.

Не изменяет production-код. Возвращает короткий технический отчёт и измеримые результаты.

#### Audio recording probe agent

Проверяет:

- permission flow;
- доступные input devices;
- запись через Qt Multimedia/Core Audio;
- формат mono PCM WAV;
- поведение при stop/pause/interruption;
- частичное восстановление файла.

Не изменяет UI и Session Engine.

#### Whisper benchmark agent

Проверяет:

- large-v3-turbo;
- large-v3;
- Metal/CPU;
- timestamps;
- JSON output;
- длительность обработки;
- ошибки на длинной записи.

Не скачивает модели и не изменяет пользовательские модели.

#### Session Engine/test agent

Проектирует таблицу переходов и unit tests для конечного автомата. Не принимает решений по TTS и не меняет UI.

#### Security/privacy review agent

Проверяет:

- отсутствие сетевых вызовов;
- пути хранения;
- удаление временного аудио;
- отсутствие автоматического скачивания моделей;
- утечки в логах;
- обработку разрешений и ошибок.

### Правила координации

- Lead agent остаётся ответственным за финальные решения.
- Все субагенты сначала читают AGENTS.md и этот документ.
- Субагенты не редактируют одни и те же файлы параллельно.
- Субагенты не изменяют AGENTS.md, concept.md и этот spec без согласования с lead agent.
- Результат субагента должен содержать: проверенный факт, метод проверки, ограничения, рекомендацию.
- Предположение нельзя выдавать за факт.
- Если задача маленькая, субагента запускать не нужно.
- Параллельно можно запускать только независимые read-only probes.

## 6. Структура проекта

Рекомендуемая структура production-проекта:

    <корень репозитория>/
      AGENTS.md
      docs/
        concept.md
        implementation-spec.md
        decisions/
      pyproject.toml
      src/
        diloger/
          app/
          qml/
          domain/
          content/
          session/
          audio/
          tts/
          transcription/
          storage/
          settings/
          resources/
      tests/
        unit/
        integration/
        fixtures/
        fakes/
      requirements-dev.txt
      work/

Рекомендуется использовать pyproject.toml и venv. Для QML-файлов использовать официальный PySide6 tooling. Названия каталогов можно адаптировать, но границы ответственности сохраняются.

work/ предназначен для временных probes, benchmark outputs и диагностических файлов. В production bundle временные модели и записи не включать.

## 7. Domain model

### Prompt

Prompt должен содержать только данные, нужные для выдачи реплики:

    id
    ordinal
    text
    sourceLine

id может быть стабильным hash от source path, line number и текста либо локальным идентификатором. Агент выбирает один вариант и покрывает его тестами.

### ContentSet

    id
    sourcePath
    displayName
    format
    contentFingerprint
    prompts[]

Поддерживаемые расширения MVP: txt и md.

### Content parser

Правила MVP:

1. Прочитать UTF-8.
2. Разбить на строки.
3. Удалить BOM, если есть.
4. Trim leading/trailing whitespace.
5. Пустые строки игнорировать.
6. Каждая непустая строка становится одним Prompt.
7. Не интерпретировать заголовки, Markdown, speaker labels или YAML.
8. Если файл не читается, вернуть typed error с путём и причиной.
9. Если после очистки нет ни одной реплики, показать пользователю понятное сообщение.

### SessionConfiguration

    mode: spoken | written
    spokenProgression: fixedTimer | manual
    writtenPrompt: audio | text
    order: sequential | randomWithoutRepetition
    fixedDelaySeconds
    recordingEnabled
    keepRecording
    saveTypedAnswers
    ttsProvider
    ttsVoice
    ttsRate
    whisperCLIPath
    whisperModelPath
    libraryPath
    sessionsPath

Недопустимые значения конфигурации должны обнаруживаться до старта сессии.

### Session

    sessionId
    startedAt
    endedAt
    sourcePath
    sourceFingerprint
    configurationSnapshot
    actualPromptOrder[]
    promptsReached
    currentIndex
    status
    eventLog[]
    recordingState
    transcriptionState
    typedAnswers[]

configurationSnapshot обязателен: будущие настройки не должны менять смысл уже завершённой сессии.

### SessionEvent

Каждое событие должно иметь:

    eventId
    type
    timestamp
    promptId, если применимо
    metadata, если применимо

Минимальные типы:

- sessionStarted;
- contentLoaded;
- promptStart;
- ttsStarted;
- ttsFinished;
- ttsFailed;
- repeatPressed;
- revealShown;
- skipPressed;
- timerStarted;
- nextPressed;
- paused;
- resumed;
- recordingStarted;
- recordingStopped;
- sessionStopped;
- contentExhausted;
- transcriptionStarted;
- transcriptionFinished;
- transcriptionFailed;
- sessionFinished.

Event log служит для технического воспроизведения временной шкалы. Он не содержит оценки ответа.

## 8. Session Engine

Session Engine должен быть тестируемым без QML, реального микрофона и реального Whisper.

### Состояния

    idle
    loading
    speakingPrompt
    waitingForManualAdvance
    waitingForTimer
    paused
    contentExhausted
    finishing
    finished
    failed

### Основные переходы

| Текущее состояние | Событие | Новое состояние | Обязательное действие |
|---|---|---|---|
| idle | start | loading | загрузить и проверить ContentSet |
| loading | content valid | speakingPrompt | выбрать первый Prompt |
| loading | content invalid | failed | показать ошибку без запуска аудио |
| speakingPrompt | TTS finished + fixed timer | waitingForTimer | запустить timer после TTS completion |
| speakingPrompt | TTS finished + manual | waitingForManualAdvance | ждать Next |
| waitingForTimer | timer fired | speakingPrompt или contentExhausted | перейти к следующему Prompt |
| waitingForManualAdvance | Next | speakingPrompt или contentExhausted | записать nextPressed |
| any active state | Repeat | speakingPrompt | не увеличивать индекс Prompt |
| any active state | Reveal | same state | показать текст без изменения порядка |
| any waiting state | Skip | speakingPrompt или contentExhausted | увеличить индекс, записать skip |
| any active state | Pause | paused | остановить progression/timer |
| paused | Resume | прежнее waiting/speaking состояние | продолжить или replay текущего Prompt |
| any non-finished | Stop | finishing | корректно завершить TTS/timer/recording |
| last Prompt reached | exhaustion | contentExhausted | остановиться и ждать решения пользователя |

### Правила

- Timer начинается после TTS completion, а не после TTS start.
- Manual Mode не имеет автоматического перехода.
- Repeat не создаёт новый Prompt и не меняет фактический порядок.
- Repeat в Fixed Timer по умолчанию запускает новый timer после повторной озвучки.
- Reveal не считается ответом и не меняет event order.
- Skip не анализирует ответ.
- После последнего Prompt автоматического wrap-around нет.
- Stop не должен silently discard запись, которую нельзя восстановить.
- Whisper не является dependency Session Engine.

### Pause

В MVP Pause останавливает progression и timer. Запись, если она включена, остаётся непрерывной, чтобы не ломать временную шкалу. Если TTS уже звучит, допустимо остановить текущий процесс и при Resume повторить текущую реплику целиком.

## 9. Spoken Mode UX

### Fixed Timer

Поток:

    load current prompt
    start recording, если enabled
    start TTS
    wait TTS completion
    start configured delay
    show remaining timer/progress
    advance

Предустановки:

- 1 second;
- 2 seconds;
- 3 seconds;
- 5 seconds;
- 10 seconds;
- 15 seconds;
- custom positive value.

Нельзя использовать микрофон для изменения длительности или досрочного перехода.

### Manual Advance

Поток:

    load current prompt
    start TTS
    wait completion
    wait indefinitely
    user presses Next

Ручной режим необходим для длинных свободных ответов.

### Training screen

В Spoken Mode по умолчанию отображать:

- название набора или имя файла;
- номер текущей реплики и общее количество;
- текущий режим;
- статус TTS;
- статус recording, если включён;
- статус Pause;
- доступные controls.

Текст реплики скрыт. Reveal отображает его временно или до повторного нажатия, но не меняет режим.

## 10. Written Mode UX

Written Mode имеет два независимых варианта:

### Audio Prompt

- вопрос произносится;
- текст вопроса скрыт;
- пользователь печатает ответ;
- переход по Next;
- запись микрофона в этом режиме не обязательна и по умолчанию недоступна;
- сохранение ответа зависит от saveTypedAnswers.

### Text Prompt

- вопрос показывается;
- TTS не требуется;
- пользователь печатает ответ;
- переход по Next;
- сохранение ответа зависит от saveTypedAnswers.

Если saveTypedAnswers выключен, текст ответа не должен попадать в постоянный transcript. Он может существовать только в памяти до перехода или закрытия.

## 11. Keyboard and pointer controls

Базовые shortcuts:

| Действие | Клавиша |
|---|---|
| Pause/Resume | Space |
| Next | Return |
| Repeat | R |
| Reveal | V |
| Skip | S |
| Stop | Escape |

В UI должны быть крупные кнопки тех же действий. Горячие клавиши не должны перехватывать ввод ответа в Text Prompt Written Mode.

Внешние педали, MIDI и voice commands не входят в MVP.

## 12. Recording subsystem

Recording должен быть независимым сервисом:

    Recorder
      prepare()
      start()
      stop()
      currentFile()
      state()
      recoverPartialRecording()

Требования:

- permission check до старта;
- пользовательское явное включение;
- стабильный локальный WAV;
- предпочтительно mono PCM 16 kHz;
- отсутствие VAD;
- отсутствие real-time transcription;
- возможность безопасно остановить запись;
- обработка interruption и device removal;
- запись не должна зависеть от того, завершил ли пользователь ответ.

### Retention policy

По умолчанию:

1. писать во временный файл;
2. после нормального завершения передать его в Whisper;
3. после успешной транскрипции удалить;
4. если Keep recording включён, переместить или скопировать в session folder;
5. при ошибке транскрипции не удалять единственную копию без явного решения.

Если приложение завершилось во время записи:

- при следующем запуске обнаружить orphan temporary recording;
- предложить сохранить, повторно обработать или удалить;
- не удалять автоматически файл, если есть риск потери данных.

## 13. TTS subsystem

### Interface responsibilities

TTS adapter должен скрывать конкретный механизм вызова. Session Engine работает только с абстракцией:

    speak(text, voice, rate) → completion/failure
    stop()
    availableVoices()
    isAvailable()

### First implementation

Проверить использование macOS say с голосами Kokoro Heart и Kokoro Michael. Child process должен:

- запускаться без shell interpolation пользовательского текста;
- корректно завершаться;
- иметь timeout/failure path;
- возвращать exit status;
- не писать пользовательский текст в постоянные логи.

Не использовать небезопасную конкатенацию shell-команд. Передавать аргументы массивом Process arguments.

### Playback and caching

Динамическая озвучка — default MVP. Сложный кэш не нужен.

Кэш можно добавить только после измерения заметной задержки. Если кэш появится:

- ограничить размер;
- хранить fingerprint текста, voice и rate;
- не сохранять кэш в session archive без причины;
- удалять по LRU или настройке.

## 14. Whisper subsystem

Whisper запускается только после Stop или нормального окончания списка и только если есть recording.

### Input

- локальный WAV;
- конкретный model path из configuration snapshot;
- language en;
- offline mode;
- timestamps enabled для сегментов;
- JSON output желательно сохранять как raw result.

### Process control

Transcription Service должен:

- проверить наличие executable;
- проверить наличие модели;
- сформировать аргументы без shell injection;
- запустить subprocess;
- собирать stdout/stderr отдельно;
- поддерживать cancellation;
- сообщать progress/state;
- проверять exit code;
- не скрывать crash/timeout;
- сохранять raw output при успешном завершении;
- не менять Session Engine progression.

### Model benchmark

В Phase 0 агент создаёт таблицу:

| Model | File size | Duration | Peak memory | Backend | Timestamps | Result | Notes |
|---|---:|---:|---:|---|---|---|---|
| large-v3-turbo | measure | measure | measure | Metal/CPU | check | check | |
| large-v3 | measure | measure | measure | Metal/CPU | check | check | |

Не подменять измерения общими заявлениями из интернета.

### Fallback

Если Whisper недоступен:

- сессию не считать полностью потерянной;
- показать понятную ошибку;
- сохранить audio, если Keep recording включён или если удаление небезопасно;
- разрешить повторить транскрипцию после исправления настроек;
- не создавать пустой transcript с видом успешного результата.

## 15. Transcript

### Raw JSON

Хранит:

- session metadata;
- model path;
- runtime path/version, если доступен;
- Whisper raw JSON;
- event log;
- processing status;
- errors.

### Markdown export

Рекомендуемая структура:

    # Dialogue Trainer Session

    Date: ...
    Source: ...
    Mode: ...
    Duration: ...
    Recording retained: yes/no
    Whisper model: ...

    ## Prompt 1

    [prompt text, если сохранение prompt допустимо]

    ### Transcript

    [фактический текст Whisper без исправлений]

Если точное сопоставление временных сегментов с Prompt ненадёжно, экспортировать:

    ## Complete transcript

    [единая расшифровка]

Запрещены:

- исправление grammar;
- исправление spelling;
- перевод;
- summary;
- semantic labels;
- оценка;
- выдуманные границы ответа.

## 16. Timestamp alignment

Система имеет:

- время загрузки Prompt;
- время начала TTS;
- время окончания TTS;
- время запуска timer;
- время Next/Skip/Repeat;
- время Pause/Resume;
- время завершения сессии.

Whisper имеет сегментные timestamps.

Допустим только технический best-effort mapping. Минимальная стратегия:

1. сохранить raw Whisper segments;
2. сопоставить segment interval с event intervals;
3. если confidence/границы недостаточны, отказаться от разбиения;
4. вывести полный transcript.

Нельзя использовать timestamp mapping для ответа на вопрос «правильно ли пользователь ответил».

## 17. Storage and configuration

### Defaults

Дефолты должны быть переносимыми и не содержать обязательного пользовательского пути:

- Library directory: user-selected;
- Sessions directory: user-selected;
- Whisper CLI: auto-discover plus manual path;
- Whisper model: auto-detect known local paths plus manual path;
- TTS voice: Kokoro Heart, если доступен;
- TTS rate: sensible default, проверяемый на слух;
- Fixed Timer: 5 seconds;
- recording: disabled;
- Keep recording: disabled;
- Save typed answers: disabled.

### Config schema

    {
      libraryPath,
      sessionsPath,
      whisperCLIPath,
      whisperModelPath,
      ttsProvider,
      ttsVoice,
      ttsRate,
      defaultDelaySeconds,
      recordingEnabledByDefault,
      keepRecordingByDefault,
      saveTypedAnswersByDefault
    }

Нельзя хранить в config API keys, потому что облачные сервисы не используются.

### Session directory

Если данные нужно сохранить:

    sessions/
      YYYY-MM-DD_HH-mm-ss_<short-id>/
        session.json
        transcript.json
        transcript.md
        recording.wav

recording.wav существует только при явном Keep recording или при необходимости восстановления после ошибки.

## 18. Error handling matrix

| Ситуация | Поведение |
|---|---|
| Нет permission на микрофон | Объяснить проблему; разрешить продолжить без записи или открыть настройки |
| Нет input device | Предупредить; отключить запись; не ломать Written Mode |
| Микрофон исчез во время записи | Остановить запись безопасно; сохранить partial file; сообщить |
| TTS недоступен | Spoken Mode не стартует; предложить проверить voice/fallback; Text Prompt остаётся доступен |
| TTS process failed | Записать техническую ошибку; предложить retry/fallback/Stop |
| Whisper CLI отсутствует | Отложить transcription; сохранить audio при необходимости; показать путь настройки |
| Whisper model отсутствует | Не скачивать; предложить выбрать существующую модель |
| Неправильная модель | Показать ошибку runtime; не удалять запись |
| Whisper crash/timeout | Сохранить diagnostics и audio; дать retry |
| Transcription cancelled | Не считать успехом; сохранить временный audio до решения |
| Недостаточно места | Остановить запись безопасно; предупредить; не продолжать скрытую потерю данных |
| App closed during recording | При следующем запуске обнаружить orphan file |
| Пустой TXT/MD | Не запускать сессию; указать причину |
| Неподдерживаемое расширение | Отклонить с понятным сообщением |
| Не читается encoding | Сообщить path и причину; не молча пропускать файл |
| Пользовательский Stop | Завершить текущую сессию; применить retention policy |
| Последний Prompt | Остановиться на нём; не loop; ждать решения |

## 19. UI screens

### Library

Минимум:

- выбранная Library directory;
- список TXT/MD;
- drag-and-drop area;
- Start;
- Settings.

### Session Setup

Минимум:

- выбранный source;
- Spoken/Written;
- Sequential/Random without repetition;
- Fixed Timer/Manual для Spoken;
- delay;
- Audio Prompt/Text Prompt для Written;
- Record toggle;
- Keep recording;
- Save typed answers;
- voice/rate;
- Start.

### Training

Состояние и controls, а не редактор текста.

### Finished

Минимальные объективные данные:

- duration;
- prompts reached;
- recording status;
- transcription status;
- buttons Retry transcription, Export Markdown, Keep/Delete audio, Close.

Не показывать score, grade, skill level или AI comments.

### Settings

- Library path;
- Sessions path;
- Whisper CLI path;
- model path;
- voice;
- rate;
- default timer;
- default recording policy;
- keyboard shortcut reference;
- privacy statement.

## 20. Accessibility and usability

MVP должен иметь:

- keyboard control;
- видимое состояние Pause;
- видимое состояние Recording;
- читаемый размер шрифта;
- достаточный контраст;
- крупные controls;
- отсутствие мерцания, зависящего от TTS;
- понятную индикацию busy/transcribing;
- корректное поведение при фокусе текстового поля.

Не нужно строить enterprise accessibility framework. Нужно обеспечить возможность пользоваться приложением без постоянного мышления о мыши.

## 21. Testing requirements

### Unit tests

Обязательны:

1. Parser:
   - пустые строки;
   - whitespace;
   - BOM;
   - UTF-8;
   - пустой файл;
   - unsupported extension.
2. Order:
   - sequential;
   - random without repetition;
   - no duplicates within one session;
   - last item behaviour.
3. Session Engine:
   - Fixed Timer starts after TTS finished;
   - Manual waits for Next;
   - Repeat does not increment index;
   - Skip increments index;
   - Reveal does not change progression;
   - Pause freezes timer;
   - Resume restores/replays according to policy;
   - Stop finalizes;
   - content exhaustion does not loop.
4. Retention:
   - delete temp audio after successful transcription;
   - retain with Keep recording;
   - retain on transcription failure;
   - recover orphan file.
5. Configuration validation.

### Integration tests

Use fake adapters for:

- TTS;
- recorder;
- Whisper process;
- clock/timer.

Integration tests must not require a real microphone or a real 3 GB model in every test run.

### Manual QA

На целевой машине проверить:

- MOTU M4 как input/output device;
- built-in microphone fallback;
- Kokoro Heart;
- Kokoro Michael;
- keyboard shortcuts;
- Fixed Timer;
- Manual Advance;
- Repeat;
- Reveal;
- Skip;
- Pause/Resume;
- Stop;
- recording off;
- recording on;
- Keep recording;
- transcription with both models;
- app termination during recording;
- no network dependency.

## 22. Security and privacy requirements

- Не использовать shell string interpolation с пользовательским текстом.
- Передавать subprocess arguments массивом.
- Не писать raw audio или полный transcript в обычные debug logs.
- Не отправлять данные в сеть.
- Не использовать telemetry.
- Не хранить лишние копии audio.
- Проверять доступ к путям перед записью.
- Не раскрывать model path или content text в системных уведомлениях без необходимости.
- Удаление временного audio должно быть проверяемым и отражаться в статусе.

## 23. Definition of Done

MVP считается готовым только если выполнены все условия:

### Functionality

- [ ] Library folder работает.
- [ ] Drag-and-drop TXT/MD работает.
- [ ] Один non-empty line импортируется как один Prompt.
- [ ] Spoken Mode произносит Prompt.
- [ ] Prompt скрыт по умолчанию.
- [ ] Reveal показывает Prompt.
- [ ] Fixed Timer работает.
- [ ] Manual Advance работает.
- [ ] Sequential работает.
- [ ] Random without repetition работает.
- [ ] Repeat/Skip/Pause/Resume/Stop работают.
- [ ] После последнего Prompt нет скрытого loop.
- [ ] Written Audio Prompt работает.
- [ ] Written Text Prompt работает.
- [ ] Save typed answers настраивается.
- [ ] Recording можно не включать.
- [ ] Recording можно включить.
- [ ] Keep recording сохраняет audio.
- [ ] Без Keep recording audio удаляется после успешной транскрипции.
- [ ] Whisper запускается только после сессии.
- [ ] Используется существующая локальная модель.
- [ ] Markdown и JSON export работают.

### Architecture

- [ ] UI не содержит доменную логику переходов.
- [ ] Session Engine тестируется отдельно.
- [ ] TTS заменяем.
- [ ] Whisper заменяем.
- [ ] Recorder заменяем.
- [ ] Content importer заменяем.
- [ ] Нет real-time STT.
- [ ] Нет answer evaluation.
- [ ] Нет cloud calls.

### Reliability

- [ ] Ошибки permission понятны.
- [ ] Ошибки TTS понятны.
- [ ] Ошибки Whisper понятны.
- [ ] Ошибки disk full не приводят к тихой потере записи.
- [ ] App termination during recording обрабатывается.
- [ ] Cancelled transcription обрабатывается.
- [ ] Unit tests проходят.
- [ ] Manual QA выполнен на Apple Silicon Mac.

## 24. Что агент должен предоставить по завершении

Агент возвращает:

1. краткое summary реализованного;
2. список созданных и изменённых файлов;
3. результаты Phase 0 benchmark;
4. команды запуска;
5. команды тестирования;
6. известные ограничения;
7. решения по рабочим гипотезам;
8. список отложенных задач;
9. подтверждение, что запрещённые функции не добавлены.

Если часть MVP не реализована, агент обязан написать это прямо. Нельзя называть MVP готовым при наличии невыполненных обязательных пунктов.

## 25. Критерий правильной реализации

Правильная реализация — это не приложение с максимальным количеством функций.

Правильная реализация:

    надёжно произносит пользовательскую реплику
            ↓
    даёт пользователю время ответить
            ↓
    переводит сессию по таймеру или по Next
            ↓
    по желанию записывает пользователя
            ↓
    после сессии локально делает transcript
            ↓
    не оценивает пользователя и не создаёт лишнего интеллекта

Если для реализации функции требуется нарушить этот принцип, функция не входит в MVP.
