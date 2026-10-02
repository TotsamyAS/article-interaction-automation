<script lang="ts">
  let { oncomplete, onclose }: { oncomplete: () => void; onclose: () => void } = $props();
  let slide = $state(0);
  const titles = ['Что это за датасет', 'Пример одной задачи', 'Как устроено решение', 'Что делает группировка', 'Чем отличаются M1–M5', 'Какие данные сохраняются', 'Предпросмотр и итог'];
  const total = titles.length;
  function next() { if (slide < total - 1) slide += 1; else oncomplete(); }
</script>

<div class="intro-backdrop" role="presentation">
  <section class="intro-deck" role="dialog" aria-modal="true" aria-label="Инструктаж перед экспериментом">
    <header class="intro-header">
      <div><p class="eyebrow">Инструктаж · {slide + 1}/{total}</p><h2>{titles[slide]}</h2></div>
      <button class="secondary compact" aria-label="Закрыть инструктаж" onclick={onclose}>Закрыть</button>
    </header>
    <div class="intro-progress" style={`--steps:${total}`} aria-hidden="true">{#each Array(total) as _, i}<span class:active={i <= slide}></span>{/each}</div>

    <div class="intro-slide">
      {#if slide === 0}
        <div class="dataset-graph">
          <div class="graph-node graph-status"><strong>Статус</strong><span>Открыта</span></div>
          <div class="graph-node graph-priority"><strong>Приоритет</strong><span>Высокий</span></div>
          <div class="graph-node graph-assignee"><strong>Исполнитель</strong><span>Анна</span></div>
          <div class="graph-node graph-epic"><strong>Направление</strong><span>Платежи</span></div>
          <div class="graph-node graph-main">
            <span class="schema-id">TASK-042</span>
            <strong>Исправить ошибку оплаты</strong>
            <small>Одна вершина графа = одна задача в таблице</small>
          </div>
          <div class="graph-node graph-sprint"><strong>Рабочий цикл</strong><span>Sprint 18</span></div>
          <div class="graph-node graph-deadline"><strong>Дедлайн</strong><span>2026-10-12</span></div>
          <div class="graph-node graph-labels"><strong>Метки</strong><span>bug · checkout</span></div>
          <div class="graph-node graph-estimate"><strong>Оценка</strong><span>8 часов</span></div>
        </div>
        <p class="intro-copy">Датасет — это реестр рабочих задач команды. В центре схемы — <b>одна задача</b>, а вокруг — её поля. В эксперименте вы всегда работаете именно с такими задачами: фильтруете их, группируете и находите нужные подмножества.</p>
      {:else if slide === 1}
        <div class="schema-visual">
          <div class="schema-record"><span class="schema-id">TASK-042</span><strong>Исправить ошибку оплаты</strong><span>Статус: Открыта</span><span>Приоритет: Высокий</span><span>Исполнитель: Анна</span></div>
          <div class="schema-fields">
            <span>направление работ: Платежи</span><span>рабочий цикл: Sprint 18</span><span>дата создания: 2026-09-14</span><span>дедлайн: 2026-10-12</span><span>метки: bug, checkout</span><span>оценка: 8 часов</span>
          </div>
        </div>
        <p class="intro-copy">Пример показывает, как одна строка таблицы превращается в объект анализа. В формулировках задач будут упоминаться именно эти атрибуты: статус, приоритет, исполнитель, эпик/направление, рабочий цикл, дедлайн, метки и оценка в часах.</p>
      {:else if slide === 2}
        <div class="pipeline-visual">
          <div><b>1</b><strong>Фильтры</strong><small>какие строки оставить</small></div><i>→</i>
          <div><b>2</b><strong>Группировка</strong><small>как разбить строки</small></div><i>→</i>
          <div><b>3</b><strong>Итог</strong><small>что посчитать внутри группы</small></div><i>→</i>
          <div><b>4</b><strong>Экстремум</strong><small>какую группу выбрать</small></div><i>→</i>
          <div><b>5</b><strong>Вывод</strong><small>таблица или CSV</small></div>
        </div>
        <p class="intro-copy">Не каждое задание использует все пять шагов. Простые задачи заканчиваются после фильтрации. Сложные C3 доходят до группировки, подсчёта и выбора максимума/минимума. Если в задании указан CSV, выберите формат CSV в запросе — отдельный файл результата скачивать не нужно.</p>
      {:else if slide === 3}
        <div class="group-chart">
          <div class="chart-row"><span>Анна</span><div style="--bar: 82%"></div><strong>5</strong></div>
          <div class="chart-row"><span>Борис</span><div style="--bar: 48%"></div><strong>3</strong></div>
          <div class="chart-row"><span>Светлана</span><div style="--bar: 66%"></div><strong>4</strong></div>
        </div>
        <div class="group-explain"><span>Группировка: Исполнитель</span><span>Итог: COUNT</span><span>Экстремум: MAX</span><strong>→ выбрана Анна</strong></div>
        <p class="intro-copy">Группировка сама ничего не «выбирает». Она только создаёт группы. Чтобы найти исполнителя с наибольшим числом задач, после группировки нужен итог <b>Количество</b>, а затем экстремум <b>Максимум</b>.</p>
      {:else if slide === 4}
        <div class="mode-grid">
          <article><b>M1</b><strong>Форма</strong><span>поля и переключатели</span></article>
          <article><b>M2</b><strong>Командная строка</strong><span>теги и конструкции</span></article>
          <article><b>M3</b><strong>Текст</strong><span>запрос своими словами</span></article>
          <article><b>M4</b><strong>Голос</strong><span>сказать запрос вслух</span></article>
          <article><b>M5</b><strong>Агент</strong><span>описать цель выполнения</span></article>
        </div>
        <p class="intro-copy">Во всех режимах предметная область и эталон результата одинаковые. Меняется только способ, которым вы выражаете намерение.</p>
      {:else if slide === 5}
        <div class="data-collection-grid">
          <article><strong>Ход пробы</strong><span>режим, задание, начало/конец, попытки и правильность</span></article>
          <article><strong>Действия в интерфейсе</strong><span>клики, изменения полей, нажатые клавиши, прокрутка и выборочные координаты указателя</span></article>
          <article><strong>Фокус окна</strong><span>моменты ухода со вкладки и возвращения на неё</span></article>
          <article><strong>Запросы к системе</strong><span>время начала/окончания предпросмотра, распознавания и итоговой проверки</span></article>
          <article><strong>M3 / M5</strong><span>введённый текст, полученный Query; для M5 — техническая траектория агента</span></article>
          <article><strong>M4</strong><span>длительность записи, расшифровка и технические параметры распознавания; исходный аудиофайл не хранится</span></article>
        </div>
        <div class="privacy-note"><strong>Не записываются:</strong> изображение экрана, камера, содержимое других вкладок и программ. Текст M3/M5 и аудио M4 обрабатываются через RouterAI; сервер стенда сохраняет результаты обработки, но не сохраняет исходную аудиозапись M4.</div>
        <p class="intro-copy">Эти данные нужны, чтобы сравнить способы взаимодействия по времени, числу попыток и последовательности действий. Они записываются только во время активных экспериментальных проб.</p>
      {:else}
        <div class="preview-flow">
          <div class="preview-card"><strong>Предпросмотр</strong><span>можно повторять</span><span>показывает таблицу и Query</span><em>не расходует попытку</em></div>
          <i>→</i>
          <div class="submit-card"><strong>Итоговый ответ</strong><span>фиксирует именно показанный результат</span><span>после него оценивается правильность</span></div>
        </div>
        <p class="intro-copy">Используйте предпросмотр, чтобы убедиться, что фильтры, группы и сортировка дали ожидаемые данные. Только после этого нажимайте итоговую кнопку. Во время активной пробы стенд сохраняет перечисленный на предыдущем слайде журнал взаимодействий для последующего анализа исследования.</p>
      {/if}
    </div>

    <footer class="intro-footer">
      <button class="secondary" disabled={slide === 0} onclick={() => slide -= 1}>Назад</button>
      <div class="intro-dots">{#each Array(total) as _, i}<button aria-label={`Слайд ${i + 1}`} class:active={i === slide} onclick={() => slide = i}></button>{/each}</div>
      <button class="primary" onclick={next}>{slide === total - 1 ? 'Понятно, перейти к стенду' : 'Далее'}</button>
    </footer>
  </section>
</div>

<style>
  .intro-progress { grid-template-columns: repeat(var(--steps), 1fr); }
  .dataset-graph { width: min(900px, 96%); margin: 0 auto; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; align-items: center; }
  .graph-node { padding: 16px 18px; border-radius: 18px; background: #f8fafc; border: 1px solid #cbd5e1; display: grid; gap: 6px; text-align: center; box-shadow: 0 8px 22px rgba(15,23,42,.05); }
  .graph-node strong { font-size: 1rem; }
  .graph-node span, .graph-node small { color: #64748b; }
  .graph-main { min-height: 160px; border: 2px solid #2563eb; background: linear-gradient(180deg, #eff6ff, #ffffff); box-shadow: 0 14px 34px rgba(37,99,235,.16); }
  .graph-main strong { font-size: 1.12rem; color: #0f172a; }
  .graph-status { grid-column: 2; }
  .graph-priority { grid-column: 1; }
  .graph-assignee { grid-column: 3; }
  .graph-epic { grid-column: 1; }
  .graph-main { grid-column: 2; }
  .graph-sprint { grid-column: 3; }
  .graph-deadline { grid-column: 1; }
  .graph-labels { grid-column: 2; }
  .graph-estimate { grid-column: 3; }
  .data-collection-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
  .data-collection-grid article { padding: 16px; border: 1px solid #dbe3ef; border-radius: 16px; background: #f8fafc; display: grid; gap: 7px; }
  .data-collection-grid span { color: #526176; line-height: 1.45; }
  .privacy-note { max-width: 900px; margin: 0 auto; padding: 14px 16px; border-radius: 14px; background: #ecfdf5; border: 1px solid #86efac; color: #28543a; line-height: 1.5; }
  @media (max-width: 860px) {
    .dataset-graph, .data-collection-grid { grid-template-columns: 1fr; }
    .graph-status, .graph-priority, .graph-assignee, .graph-epic, .graph-main, .graph-sprint, .graph-deadline, .graph-labels, .graph-estimate { grid-column: auto; }
  }
</style>
