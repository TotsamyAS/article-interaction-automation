export type Mode = 'M1' | 'M2' | 'M3' | 'M4' | 'M5';
export type TrialStatus = 'pending' | 'active' | 'correct' | 'incomplete';
export type Role = 'participant' | 'researcher';

export interface TaskRecord {
  id: string;
  title: string;
  status: string;
  priority: string;
  assignee: string | null;
  epic: string;
  sprint: string;
  created_at: string;
  deadline: string | null;
  labels: string[];
  estimate_hours: number;
}

export type FieldName = 'id' | 'title' | 'status' | 'priority' | 'assignee' | 'epic' | 'sprint' | 'created_at' | 'deadline' | 'labels' | 'estimate_hours';
export type FilterOperator = 'eq' | 'neq' | 'gt' | 'lt' | 'gte' | 'lte' | 'in' | 'not_in' | 'is_null' | 'not_null';

export interface QueryFilter {
  field: FieldName;
  operator: FilterOperator;
  value?: string | number | Array<string | number> | null;
  logic?: 'AND' | 'OR';
}

export interface Query {
  filters: QueryFilter[];
  grouping: { field: 'status' | 'priority' | 'assignee' | 'epic' | 'sprint' | null; aggregation: 'COUNT' | 'SUM' | 'AVG' | 'MAX' | 'MIN' | null; aggregation_field: 'estimate_hours' | null };
  extremum: { direction: 'max' | 'min' | null; metric: 'count' | 'sum' | 'avg' | 'max' | 'min' | null };
  sorting: { field: FieldName | null; direction: 'asc' | 'desc' | null };
  output: { format: 'table' | 'csv' | 'chart' | 'summary'; scope: 'all_records' | 'extremum_group' | 'aggregate_only' };
}

export interface GroupResult { key: string | null; value: string | null; record_ids: string[] }
export interface QueryResult {
  record_ids: string[];
  records: TaskRecord[];
  value: string | null;
  groups: GroupResult[];
  selected_groups: Array<string | null>;
  tie: boolean;
}

export interface AttemptSummary { id: string; ordinal: number; correct: boolean; started_ms: number; finished_ms: number }
export interface TrialView {
  id: string;
  session_id: string;
  position: number;
  block_index: number;
  mode: Mode;
  task_id: string;
  status: TrialStatus;
  started_ms: number | null;
  ended_ms: number | null;
  end_reason: string | null;
  prompt: string | null;
  tci: number;
  level: number;
  mode_available: boolean;
  deadline_ms: number | null;
  attempts: AttemptSummary[];
  next_event_sequence: number;
  last_event_offset_ms: number;
  metrics: {
    actual: { elapsed_ms: number | null; Tcorrect_ms: number | null; Tfirst_ms: number | null; Tuser_active_ms: number; A1: number | null; attempts: number; Nretry: number };
    analysis: { Tcorrect_ms: number | null; A1: number | null; Nretry: number };
    incomplete: boolean;
    end_reason: string | null;
  };
}

export interface SessionView {
  id: string;
  participant_code: string;
  kind: 'experiment' | 'practice';
  sequence_no: number;
  created_ms: number;
  complete: boolean;
  manifest: { protocol: { reference_date: string; trial_limit_seconds: number; attempt_limit: number; break_seconds: number; [key: string]: unknown }; [key: string]: unknown };
  trials: TrialView[];
}

export interface MeResponse { participant_code: string; role: Role; sessions: SessionView[] }
export interface ManualQueryHelp { placeholder: string; instruction: string; examples: string[]; suggestions: string[] }
export interface AttemptView extends AttemptSummary {
  trial_id: string;
  trial_status: TrialStatus;
  end_reason: string | null;
  result: QueryResult;
  Texec_ms: number;
  export_url: string | null;
}
