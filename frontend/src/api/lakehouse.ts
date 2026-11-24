/** 湖表数据文件差异视图、回收复核与历史清理专用接口。 */
import { request } from './client'

export type DiffFileItem = {
  category: string
  object_id: string
  table: string
  path: string
  checksum: string
  size_bytes: number
  origin_snapshot_id: string
  paired_object_id: string | null
}

export type DiffView = {
  diff_id: string
  table: string
  base_snapshot_id: string
  target_snapshot_id: string
  created_at: string
  removed: DiffFileItem[]
  rewritten: DiffFileItem[]
  added: DiffFileItem[]
  removed_count: number
  rewritten_count: number
  added_count: number
  frozen: boolean
}

export type ReviewItem = {
  object_id: string
  table: string
  path: string
  checksum: string
  origin_snapshot_id: string
  source_category: string
}

export type LiveReference = {
  object_id: string
  path: string
  referring_snapshots: string[]
  released: boolean
}

export type Review = {
  review_id: string
  diff_id: string
  table: string
  base_snapshot_id: string
  created_at: string
  operator: string
  status: string
  receipt_ids: string[]
  items: ReviewItem[]
  item_count: number
  live_reference?: LiveReference[]
}

export type LedgerRow = {
  receipt_id: string
  review_id: string
  table: string
  object_id: string
  path: string
  checksum: string
  origin_snapshot_id: string
  operator: string
  decided_at: string
  actually_released: boolean
  reason: string
  referring_snapshots: string[]
}

export type Receipt = {
  receipt_id: string
  review_id: string
  table: string
  operator: string
  status: string
  started_at: string
  finished_at: string | null
  attempts: { operator: string; at: string; action: string }[]
  items: LedgerRow[]
  released_count: number
  skipped_count: number
  idempotent_replay?: boolean
}

export type SnapshotFile = {
  object_id: string
  table: string
  path: string
  size_bytes: number
  checksum: string
  released: boolean
  released_at: string | null
}

export type SnapshotInfo = {
  snapshot_id: string
  table: string
  label: string
  files: SnapshotFile[]
  file_count: number
}

export type ReadableReport = {
  snapshot_id: string
  readable: boolean
  missing_or_released: string[]
  referenced_files: SnapshotFile[]
}

export type VacuumResult = {
  ok: boolean
  interrupted: boolean
  message?: string
  receipt: Receipt
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error((await response.json()).detail ?? '请求失败')
  return (await response.json()) as T
}

async function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail ?? '操作失败')
  return payload as T
}

export async function listSnapshots(): Promise<SnapshotInfo[]> {
  const payload = await getJson<{ items: SnapshotInfo[] }>('/api/lakehouse/snapshots')
  return payload.items
}

export async function createDiff(base: string, target: string): Promise<DiffView> {
  return postJson('/api/lakehouse/diffs', {
    base_snapshot_id: base,
    target_snapshot_id: target,
  })
}

export async function listDiffs(): Promise<DiffView[]> {
  const payload = await getJson<{ items: DiffView[] }>('/api/lakehouse/diffs')
  return payload.items
}

export async function attachFile(
  snapshotId: string,
  file: { table: string; path: string; checksum: string; size_bytes: number },
): Promise<{ attached: boolean }> {
  return postJson(`/api/lakehouse/snapshots/${snapshotId}/attach`, file)
}

export async function createReview(diffId: string, operator: string): Promise<Review> {
  return postJson('/api/lakehouse/reviews', { diff_id: diffId, operator })
}

export async function listReviews(): Promise<Review[]> {
  const payload = await getJson<{ items: Review[] }>('/api/lakehouse/reviews')
  return payload.items
}

export async function getReview(reviewId: string): Promise<Review> {
  return getJson(`/api/lakehouse/reviews/${reviewId}`)
}

export async function getNextScope(reviewId: string): Promise<{
  review_id: string
  original_scope_count: number
  pending_count: number
  pending_items: { object_id: string; path: string; origin_snapshot_id: string; referring_snapshots: string[] }[]
}> {
  return getJson(`/api/lakehouse/reviews/${reviewId}/next-scope`)
}

export async function runVacuum(
  reviewId: string,
  operator: string,
  failAfter?: number | null,
): Promise<VacuumResult> {
  return postJson('/api/lakehouse/vacuum', {
    review_id: reviewId,
    operator,
    fail_after: failAfter ?? null,
  })
}

export async function listLedger(reviewId?: string): Promise<LedgerRow[]> {
  const query = reviewId ? `?review_id=${encodeURIComponent(reviewId)}` : ''
  const payload = await getJson<{ items: LedgerRow[] }>(`/api/lakehouse/ledger${query}`)
  return payload.items
}

export async function listReceipts(reviewId?: string): Promise<Receipt[]> {
  const query = reviewId ? `?review_id=${encodeURIComponent(reviewId)}` : ''
  const payload = await getJson<{ items: Receipt[] }>(`/api/lakehouse/receipts${query}`)
  return payload.items
}

export async function getReceipt(receiptId: string): Promise<Receipt> {
  return getJson(`/api/lakehouse/receipts/${receiptId}`)
}

export async function checkReadable(snapshotId: string): Promise<ReadableReport> {
  return getJson(`/api/lakehouse/snapshots/${snapshotId}/readable`)
}
