export type Option = {
  number: number | null;
  label: string;
  description: string;
  selected: boolean;
  checked: boolean;
  kind: string;
  preview: string;
};

export type Ask = {
  question: string;
  options: Option[];
  multi: boolean;
  submit_row: string;
  tabs: string[];
  context: string;
  kind: string;
  raw: string;
};

export type Station = [string, string];

export type Card = {
  pane: string;
  name: string;
  kind: string;
  cwd: string;
  repo: string | null;
  branch: string;
  ticket: string;
  stations: Station[];
  current_station: string | null;
  status: Record<string, unknown>;
  agent_status: string;
  needs_user: boolean;
  ask: Ask | null;
  last_line: string;
  column: string;
  time_in_column: number;
  unpushed: number | null;
  context_pct: number | null;
};

export type Alert = {
  kind: string;
  message: string;
  pane: string;
  key: string;
  time: number;
};

export type Worktree = {
  repo: string;
  path: string;
  branch: string;
  uncommitted: number;
  unmerged: number;
};

export type Board = {
  cards: Card[];
  tickets: Record<string, Card[]>;
  alerts: Alert[];
  closed: Card[];
  frozen_roster: unknown[] | null;
  worktrees: Worktree[];
  remote_on: boolean;
  columns: string[];
  now: number;
};

export type ChatRow = {
  kind: string;
  text?: string;
  path?: string;
  command?: string;
  subagent_type?: string;
  answer?: string;
  ask?: { questions?: unknown[] };
  _fold?: string;
  _count?: number;
  ts?: string;
};

export type Agent = {
  pane: string;
  name: string;
  kind: string;
  cwd: string;
  repo: string | null;
  git: GitStatus | null;
  chat: ChatRow[];
  alerts: Alert[];
  supports: Record<string, boolean>;
  notes: string[];
  ask: Ask | null;
  screen_tail: string[];
  status: Record<string, unknown>;
  usage_history: [number, number][];
  error?: string;
};

export type GitFile = {
  added: string;
  removed: string;
  path: string;
  status?: string;
};

export type GitCommit = {
  sha: string;
  short: string;
  subject: string;
  date: string;
  pushed: boolean;
};

export type GitStatus = {
  repo: string;
  branch: string;
  upstream: string | null;
  ahead?: number;
  behind?: number;
  files: GitFile[];
  files_capped: boolean;
  commits: GitCommit[];
  unpushed_commits: number | null;
  has_remote: boolean;
};

export type MenuItem = {
  trigger: string;
  label: string;
  detail: string;
  kind: string;
  selected: boolean;
};

export type FolderEntry = {
  name: string;
  path: string;
  has_children: boolean;
};

export type FolderListing = {
  path: string;
  home: string;
  parent: string | null;
  can_descend: boolean;
  dirs: FolderEntry[];
};
