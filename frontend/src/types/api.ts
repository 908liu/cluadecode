export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface PageParams {
  page?: number
  page_size?: number
}

/* ---------------- auth ---------------- */

export interface AuthUser {
  id: string
  username: string
  display_name: string
}

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: AuthUser
}

export type RegistrationMode = 'open' | 'first-user' | 'invite' | 'closed'

export interface RegistrationStatus {
  registered: boolean
  mode: RegistrationMode
  registration_allowed: boolean
  invite_required: boolean
}

export interface RegisterRequest {
  username: string
  password: string
  display_name?: string
  invite_code?: string
}

export type RegisterResponse = LoginResponse

/* ---------------- system / services ---------------- */

export type ServiceType = 'chat' | 'embedding' | 'rerank'
export type ServiceSource = 'default' | 'env' | 'none'
export type RerankProtocol = 'cohere' | 'dashscope'

export interface ServiceSummary {
  id: string | null
  name: string
  type: ServiceType
  model: string
  enabled: boolean
  protocol?: RerankProtocol
}

export interface EnvServiceStatus {
  configured: boolean
  service: ServiceSummary | null
}

export interface TypeStatus {
  configured: boolean
  source: ServiceSource
  service: ServiceSummary | null
  env: EnvServiceStatus
}

export interface SystemStatus {
  ready: boolean
  available_documents: number
  reindex_required: boolean
  types: Record<ServiceType, TypeStatus>
}

export interface ApiService {
  id: string
  name: string
  type: ServiceType
  base_url: string
  model: string
  api_key_masked: string
  timeout_seconds: number
  dimensions: number | null
  protocol: RerankProtocol
  enabled: boolean
  is_default: boolean
  source: 'db'
  created_at: string
  updated_at: string
}

export interface ServicePayload {
  name: string
  type: ServiceType
  base_url: string
  api_key?: string
  model: string
  timeout_seconds: number
  dimensions: number | null
  protocol?: RerankProtocol | null
}

export type TestErrorType =
  | 'auth'
  | 'not_found'
  | 'timeout'
  | 'network'
  | 'rate_limit'
  | 'dimension_mismatch'
  | 'unsupported'
  | 'server_error'
  | 'unknown'

export interface TestConnectionResult {
  success: boolean
  latency_ms: number
  status_code: number | null
  error_type: TestErrorType | null
  message: string
}

/* ---------------- documents ---------------- */

export type DocumentStatus =
  | 'queued'
  | 'parsing'
  | 'chunking'
  | 'indexing'
  | 'available'
  | 'failed'
  | 'deleted'

export type DocumentFormat = 'PDF' | 'Word' | 'Markdown'

export type DocumentFilterStatus = 'all' | 'available' | 'processing' | 'failed' | 'trash'

export interface DocumentSummary {
  id: string
  title: string
  filename: string
  format: DocumentFormat
  size_bytes: number
  uploader: string
  status: DocumentStatus
  chunks: number
  error: string | null
  error_code: string | null
  active_version: number
  reindex_required: boolean
  progress: number
  queue_position: number | null
  deleted_at: string | null
  purge_at: string | null
  created_at: string
  updated_at: string
}

export interface DocumentListParams extends PageParams {
  q?: string
  status?: DocumentFilterStatus
  format?: 'all' | DocumentFormat
  sort?: string
}

export interface DocumentStatusItem {
  id: string
  status: DocumentStatus | 'missing'
  chunks: number
  progress: number
  queue_position: number | null
  error: string | null
  error_code: string | null
  updated_at: string
}

export interface Chunk {
  id: string
  seq: number
  page: number | null
  paragraph: string | null
  text: string
}

export interface ParseMeta {
  page_count: number | null
  paragraph_count: number | null
  char_count: number
  parser: string
  parser_version: string
  warnings: string[]
}

export interface DocumentDetail extends DocumentSummary {
  parse_meta: ParseMeta | null
}

export interface PreflightFile {
  client_id: string
  name: string
  size_bytes: number
  sha256?: string
}

export type PreflightStatus =
  | 'ok'
  | 'duplicate'
  | 'same_name'
  | 'duplicate_and_same_name'
  | 'unsupported'
  | 'too_large'
  | 'invalid'

export type ConflictAction = 'none' | 'overwrite' | 'keep_both' | 'cancel'

export interface PreflightExisting {
  document_id: string
  title: string
  size_bytes: number
  uploader: string
  active_version: number
  updated_at: string
}

export interface PreflightResult {
  client_id: string
  name: string
  status: PreflightStatus
  existing: PreflightExisting | null
  duplicate_of: PreflightExisting | null
  allowed_actions: ConflictAction[]
  message: string | null
}

export interface PreflightResponse {
  results: PreflightResult[]
  batch_conflicts?: {
    duplicate_groups: string[][]
    same_name_groups: string[][]
  }
}

export interface UploadDocumentPayload {
  file: File
  uploader_display?: string
  conflict_action: ConflictAction
  existing_document_id?: string
}

export interface ReindexTask {
  task_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  total: number
  done: number
  failed: number
  accepted_document_ids: string[]
  error_message?: string | null
}

/* ---------------- qa ---------------- */

export type RefusalReason =
  | 'empty_kb'
  | 'empty_retrieval'
  | 'insufficient_evidence'
  | 'invalid_citation'

export interface Citation {
  document_id: string
  document_title: string
  version: number
  chunk_id: string
  page: number | null
  paragraph: string | null
  snippet: string
  uploader: string
  download_url: string
}

export interface AskResponse {
  request_id: string
  answer: string
  citations: Citation[]
  refused: boolean
  refusal_reason: RefusalReason | null
  model: string
  latency_ms: number
}

/* ---------------- evaluation ---------------- */

export interface EvalDataset {
  id: string
  name: string
  description: string
  case_count: number
  answerable_count: number
  refusal_count: number
  version: string
  is_builtin: boolean
}

export interface EvalRunConfig {
  dataset_version: string
  rubric_version: string
  prompt_version: string
  top_k: number
  threshold: number
  judge_service_id: string
  judge_model: string
  generation_service_id: string
  generation_model: string
  embedding_service_id: string
  embedding_model: string
  embedding_dimensions: number
  use_rerank?: boolean
  rerank_top_n?: number
  rerank_service_id?: string
  rerank_model?: string
  index_version_id: string
  retrieval_strategy?: 'dense' | 'hybrid' | 'hybrid_rerank'
}

export interface EvalRun {
  id: string
  dataset_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  progress: { done: number; total: number }
  config_snapshot: EvalRunConfig
  error_code: string | null
  error_message: string | null
  rescore_count: number
  created_at: string
  updated_at: string
  finished_at: string | null
}

export interface EvalCaseBase {
  case_id: string
  question: string
  reference_answer: string
  gold_source: string
  expected_refusal: boolean
  tags: string[]
  difficulty: string
  answer: string
  error: string | null
}

export interface EvalCaseModel extends EvalCaseBase {
  recall_hit: boolean
  citation_valid: boolean
  ndcg_score: number | null
  model_score: number | null
  model_reason: string | null
}

export interface EvalCaseHuman extends EvalCaseBase {
  recall_hit?: boolean
  citation_valid?: boolean
  ndcg_score?: number | null
  human_score: number | null
  human_note: string
  human_scored_at: string | null
}

export interface EvalMetrics {
  total_cases: number
  recall_at_k: number | null
  mrr: number | null
  ndcg_at_k: number | null
  ndcg_k: number
  ndcg_scored: number
  citation_valid_rate: number | null
  refusal_accuracy: number | null
  model_score_avg: number | null
  model_scored: number
  coverage: number
  cases_failed: number
  avg_latency_ms: number | null
  human_scored: number
  human_score_avg: number | null
  human_model_agreement: number | null
}

export interface EvalCaseQuery extends PageParams {
  mode: 'model' | 'human'
  filter?: 'all' | 'scored' | 'unscored'
}

/* ---------------- search (P1) ---------------- */

export interface SearchRerankInfo {
  enabled: boolean
  applied: boolean
  candidates: number
  top_n: number
  error: string | null
  service_id: string | null
  model: string | null
}

export interface SearchDebugResult {
  keyword: Array<Record<string, unknown>>
  vector: Array<Record<string, unknown>>
  fused: Array<Record<string, unknown>>
  reranked: Array<Record<string, unknown>>
  final: Array<Record<string, unknown>>
  rerank: SearchRerankInfo
}

export interface SearchSettings {
  top_k: number
  rrf_k: number
  use_rerank: boolean
  rerank_top_n: number
}