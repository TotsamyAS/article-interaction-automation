<script lang="ts">
  import { api, downloadCorpus, errorMessage } from '$lib/api';
  import type { SourcePage, SourceSort } from '$lib/import-types';
  import { Role, type ProjectDetail } from '$lib/types';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import Notice from './Notice.svelte';
  import Pagination from './Pagination.svelte';
  import DistributionChart from './DistributionChart.svelte';
  import SortableTableHeader from './SortableTableHeader.svelte';
  import TagInput from './TagInput.svelte';

  let { projectId, userId, members, roster, isTeacher }: {
    projectId: string; userId: string; members: ProjectDetail['members']; roster: ProjectDetail['roster']; isTeacher: boolean;
  } = $props();
  let result = $state<SourcePage | null>(null);
  let contributor = $state('');
  let page = $state(1);
  let busy = $state(false);
  let error = $state('');
  let success = $state('');
  let sort = $state<SourceSort>('created_at');
  let direction = $state<'asc' | 'desc'>('desc');
  let selectedYears = $state<string[]>([]);
  let titleInput = $state('');
  let titleFilter = $state('');
  let includeContributor = $state(true);
  let exporting = $state(false);
  let selectedSourceIds = $state<string[]>([]);
  let allSourcesSelected = $state(false);
  let deleteDialogOpen = $state(false);
  let deleting = $state(false);
  let refreshToken = $state(0);
  const students = $derived(members.filter((member) => member.role === Role.Student));
  const pendingRoster = $derived(roster.filter((entry) => !entry.user_id));
  const corpusCount = $derived(result?.contributors.reduce((sum, item) => sum + item.count, 0) ?? 0);
  const currentPageSelected = $derived(
    !!result?.items.length && result.items.every((source) => selectedSourceIds.includes(source.id))
  );
  const selectionCount = $derived(allSourcesSelected ? corpusCount : selectedSourceIds.length);

  function changeSort(key: SourceSort) {
    direction = sort === key && direction === 'asc' ? 'desc' : 'asc';
    sort = key; page = 1;
  }
  function selectYear(year: number | null) { selectedYears = year === null ? [] : [String(year)]; page = 1; }
  function selectContributor(id: string) { contributor = contributor === id ? '' : id; page = 1; selectedYears = []; }
  function contributorCount(id: string) { return result?.contributors.find((item) => item.user.id === id)?.count ?? 0; }
  const sortDirection = (key: SourceSort) => sort !== key ? 'none' : direction === 'asc' ? 'ascending' : 'descending';

  function toggleSource(id: string, checked: boolean) {
    if (allSourcesSelected) return;
    selectedSourceIds = checked
      ? [...new Set([...selectedSourceIds, id])]
      : selectedSourceIds.filter((sourceId) => sourceId !== id);
  }

  function toggleCurrentPage(checked: boolean) {
    if (!result || allSourcesSelected) return;
    const pageIds = new Set(result.items.map((source) => source.id));
    selectedSourceIds = checked
      ? [...new Set([...selectedSourceIds, ...pageIds])]
      : selectedSourceIds.filter((sourceId) => !pageIds.has(sourceId));
  }

  function selectWholeCorpus() {
    allSourcesSelected = true;
    selectedSourceIds = [];
  }

  function clearSourceSelection() {
    allSourcesSelected = false;
    selectedSourceIds = [];
  }

  async function deleteSources() {
    if (!selectionCount || deleting) return;
    deleting = true;
    error = '';
    success = '';
    try {
      const response = await api<{ deleted: number }>(userId, '/projects/' + projectId + '/sources', {
        method: 'DELETE',
        json: { source_ids: allSourcesSelected ? [] : selectedSourceIds, all_sources: allSourcesSelected }
      });
      success = `Удалено источников: ${response.deleted}. История импортов сохранена.`;
      clearSourceSelection();
      deleteDialogOpen = false;
      page = 1;
      refreshToken += 1;
    } catch (cause) {
      error = errorMessage(cause);
    } finally {
      deleting = false;
    }
  }

  async function exportCorpus() {
    if (exporting) return;
    exporting = true;
    error = '';
    try { await downloadCorpus(userId, projectId, includeContributor); }
    catch (cause) { error = errorMessage(cause); }
    finally { exporting = false; }
  }

  $effect(() => {
    const value = titleInput.trim();
    const timer = window.setTimeout(() => {
      titleFilter = value;
      page = 1;
    }, 250);
    return () => window.clearTimeout(timer);
  });

  $effect(() => {
    const controller = new AbortController();
    const requestRevision = refreshToken;
    busy = true; error = '';
    const query = new URLSearchParams({ page: String(page), sort, direction });
    if (contributor) query.set('contributor', contributor);
    if (selectedYears[0]) query.set('year', selectedYears[0]);
    if (titleFilter) query.set('title', titleFilter);
    api<SourcePage>(userId, '/projects/' + projectId + '/sources?' + query, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted && requestRevision === refreshToken) result = data;
      })
      .catch((cause) => { if (!controller.signal.aborted) error = errorMessage(cause); })
      .finally(() => { if (!controller.signal.aborted) busy = false; });
    return () => controller.abort();
  });
</script>

<section class="panel" id="corpus">
  <div class="section-heading">
    <div><p class="eyebrow">Совместная работа</p><h2>Общий корпус</h2></div>
    <div class="actions corpus-export-actions">
      <label class="checkbox"><input type="checkbox" bind:checked={includeContributor} /> Указывать студента</label>
      <button class="secondary" disabled={exporting} onclick={exportCorpus}>
        {exporting ? 'Готовим Excel…' : 'Скачать сводный Excel'}
      </button>
    </div>
  </div>
  <Notice message={error} />
  <Notice message={success} kind="success" />
  {#if result}
    {#if isTeacher}
      <div class="student-source-summary">
        <div class="section-heading compact-heading"><div><h3>Студенты</h3><p class="small muted">Нажмите ФИО — ниже останутся источники только этого студента.</p></div>
          {#if contributor}<button class="text-button" onclick={() => selectContributor(contributor)}>Показать всех</button>{/if}</div>
        <div class="table-scroll"><table class="student-table"><thead><tr><th>ФИО</th><th>Email</th><th>Статус</th><th>Загружено источников</th></tr></thead><tbody>
          {#each students as student (student.id)}
            <tr class:active-filter={contributor === student.id}>
              <td><button class="table-link" aria-pressed={contributor === student.id} onclick={() => selectContributor(student.id)}>{student.display_name}</button></td>
              <td>{student.email}</td>
              <td><span class="badge success">В проекте</span></td>
              <td>{contributorCount(student.id)}</td>
            </tr>
          {/each}
          {#each pendingRoster as entry (entry.id)}
            <tr>
              <td>{entry.display_name}</td>
              <td>{entry.email || '—'}</td>
              <td><span class="badge">Не присоединился</span></td>
              <td>0</td>
            </tr>
          {/each}
          {#if students.length === 0 && pendingRoster.length === 0}
            <tr><td colspan="4" class="muted">Студенты пока не добавлены. Откройте «Группа и доступ», чтобы создать ссылку приглашения.</td></tr>
          {/if}
        </tbody></table></div>
      </div>
    {/if}

    <DistributionChart buckets={result.years} selectedYear={selectedYears[0] ? Number(selectedYears[0]) : null} onselect={selectYear} />
    <div class="corpus-filter-row">
      <div class="contributor-filter"><p class="small muted">Год публикации</p>
        <TagInput bind:value={selectedYears} suggestions={result.years.map((item) => String(item.year))}
          maxTags={1} replaceWhenFull allowCustom={false} ariaLabel="Отобрать год публикации"
          placeholder="Введите год из списка" onValueChange={() => page = 1} />
      </div>
      <label class="title-filter"><span class="small muted">Название</span>
        <input bind:value={titleInput} type="search" maxlength="200" placeholder="Введите часть названия" />
      </label>
    </div>
    {#if result.without_year}<p class="small muted">Без указанного года: {result.without_year}.</p>{/if}
    {#if isTeacher && corpusCount}
      <div class="actions corpus-delete-actions">
        {#if allSourcesSelected}
          <strong class="small">Выбран весь корпус: {corpusCount}</strong>
          <button class="text-button" onclick={clearSourceSelection}>Снять выбор</button>
        {:else}
          <span class="small muted">Выбрано: {selectedSourceIds.length}</span>
          <button class="secondary" onclick={selectWholeCorpus}>Выбрать весь корпус ({corpusCount})</button>
        {/if}
        <button class="secondary danger" disabled={!selectionCount || deleting} onclick={() => deleteDialogOpen = true}>
          {deleting ? 'Удаляем…' : `Удалить выбранные (${selectionCount})`}
        </button>
      </div>
    {/if}
    {#if busy}<p role="status" class="muted">Обновляем корпус…</p>
    {:else if !result.items.length}<p class="muted">{contributor || selectedYears[0] || titleFilter ? 'Нет источников по выбранным фильтрам.' : 'Принятых источников пока нет. Загрузите Excel и подтвердите импорт.'}</p>
    {:else}
      <p class="small muted">Источников: {result.total}. Предпросмотры, ошибки и дубли сюда не входят.</p>
      <div class="table-scroll"><table class="source-table"><thead><tr>
        {#if isTeacher}
          <th class="source-select-cell">
            <input type="checkbox" aria-label="Выбрать источники на текущей странице"
              checked={allSourcesSelected || currentPageSelected} disabled={allSourcesSelected}
              onchange={(event) => toggleCurrentPage(event.currentTarget.checked)} />
          </th>
        {/if}
        <SortableTableHeader label="Авторы" direction={sortDirection('author')} onclick={() => changeSort('author')} />
        <SortableTableHeader label="Название" direction={sortDirection('title')} onclick={() => changeSort('title')} />
        <SortableTableHeader label="Источник" direction={sortDirection('source')} onclick={() => changeSort('source')} />
        <SortableTableHeader label="Год" direction={sortDirection('year')} onclick={() => changeSort('year')} />
        <SortableTableHeader label="Добавил" direction={sortDirection('contributor')} onclick={() => changeSort('contributor')} />
      </tr></thead><tbody>
        {#each result.items as source (source.id)}
          <tr>
            {#if isTeacher}
              <td class="source-select-cell">
                <input type="checkbox" aria-label={'Выбрать источник «' + source.title + '»'}
                  checked={allSourcesSelected || selectedSourceIds.includes(source.id)} disabled={allSourcesSelected}
                  onchange={(event) => toggleSource(source.id, event.currentTarget.checked)} />
              </td>
            {/if}
            <td class="source-author-cell">
              <span class="source-authors small muted">{source.authors.length ? source.authors.join('; ') : 'Автор не указан'}</span>
            </td>
            <td class="source-title-cell">
              <a class="source-title-link" href={'/projects/' + projectId + '/sources/' + source.id}>{source.title}</a>
            </td>
            <td class="source-site-cell">
              {#if source.source_url && source.source_site}
                <a class="source-site-link" href={source.source_url} target="_blank" rel="noopener noreferrer">{source.source_site}</a>
              {:else}
                <span class="muted">—</span>
              {/if}
            </td>
            <td class="source-year-cell">{source.year ?? '—'}</td>
            <td class="source-contributor-cell">{source.contributor.display_name}</td>
          </tr>
        {/each}
      </tbody></table></div>
    {/if}
    <Pagination {page} total={result.total} pageSize={result.page_size} disabled={busy} onchange={(value) => page = value} />
  {:else if busy}<p role="status" class="muted">Загружаем корпус…</p>{/if}
</section>

{#if deleteDialogOpen}
  <ConfirmDialog
    title={allSourcesSelected ? 'Удалить весь корпус?' : 'Удалить выбранные источники?'}
    message={allSourcesSelected
      ? `Из общего корпуса будут удалены все ${corpusCount} источников. История импортов сохранится, после чего источники можно загрузить заново.`
      : `Из общего корпуса будут удалены выбранные источники: ${selectedSourceIds.length}. История импортов сохранится.`}
    confirmLabel="Удалить"
    danger
    onconfirm={deleteSources}
    oncancel={() => { if (!deleting) deleteDialogOpen = false; }}
  />
{/if}
