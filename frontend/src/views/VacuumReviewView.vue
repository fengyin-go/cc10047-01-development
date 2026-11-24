<template>
  <section class="page" data-module="vacuum_job">
    <header class="page-head">
      <div>
        <h2>回收复核与历史清理</h2>
        <p class="page-desc">
          历史清理从数据文件差异视图接收复核范围；执行时重新核对当前快照引用，
          仍被引用的对象只登记不释放。处置清单逐件保存原快照版本、文件身份及是否真的释放，
          同一份复核单重复提交只确认一次释放。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/snapshot_diff">返回数据文件差异视图</RouterLink>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="loadReview">
      <label class="filter-item">
        <span>回收复核单</span>
        <select v-model="selectedReviewId" data-testid="review-select">
          <option value="" disabled>请选择复核单</option>
          <option v-for="review in reviews" :key="review.review_id" :value="review.review_id">
            {{ review.review_id }}（{{ review.table }}，{{ review.item_count }} 件，{{ review.status }}）
          </option>
        </select>
      </label>
      <button class="btn primary" data-testid="review-load" type="submit">打开历史清理详情</button>
      <label class="filter-item">
        <span>值班员</span>
        <input v-model="operator" data-testid="operator" />
      </label>
      <label class="filter-item">
        <span>模拟中断（处理N件后）</span>
        <input v-model.number="failAfter" data-testid="fail-after" type="number" min="1" placeholder="留空=执行到底" />
      </label>
    </form>

    <p v-if="message" :class="lastResult?.interrupted ? 'warn-text' : 'error-text'">{{ message }}</p>

    <template v-if="currentReview">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">复核状态</span>
          <strong class="stat-value">{{ currentReview.status }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">复核范围</span>
          <strong class="stat-value">{{ currentReview.item_count }} 件</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">关联差异视图</span>
          <strong class="stat-value">{{ currentReview.diff_id }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">基准快照</span>
          <strong class="stat-value">{{ currentReview.base_snapshot_id }}</strong>
        </article>
      </div>

      <h3>复核范围与当前引用（执行时刻实时核对）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>物理路径</th>
            <th>文件身份</th>
            <th>原快照版本</th>
            <th>来源分类</th>
            <th>当前活动引用快照</th>
            <th>物理释放状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in currentReview.items" :key="item.object_id" :data-testid="`scope-${item.object_id}`">
            <td>{{ item.path }}</td>
            <td>{{ item.object_id }}</td>
            <td>{{ item.origin_snapshot_id }}</td>
            <td>{{ item.source_category === 'removed' ? '移除项' : '改写前旧对象' }}</td>
            <td>{{ liveRef(item.object_id).join('、') || '无活动引用' }}</td>
            <td>{{ liveReleased(item.object_id) ? '已释放' : '保留中' }}</td>
          </tr>
        </tbody>
      </table>

      <div class="page-actions">
        <button class="btn primary" data-testid="run-vacuum" type="button" :disabled="running" @click="execute(false)">
          {{ running ? '清理中…' : '执行清理（按当前引用重新核对）' }}
        </button>
        <button class="btn" data-testid="run-vacuum-interrupt" type="button" :disabled="running" @click="execute(true)">
          模拟保存回执中断
        </button>
        <button class="btn ghost" type="button" @click="retryFromInterrupt" :disabled="running">
          中断后重试接续
        </button>
        <button class="btn ghost" type="button" @click="duplicateSubmit" :disabled="running">
          另一值班员重复提交（验证幂等）
        </button>
      </div>

      <h3>文件处置清单（逐件回写，仅追加）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>执行回执</th>
            <th>物理路径</th>
            <th>文件身份</th>
            <th>原快照版本</th>
            <th>是否真的释放</th>
            <th>处置时间</th>
            <th>值班员</th>
            <th>处置原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in ledger" :key="`${row.receipt_id}-${row.object_id}`" :data-testid="`ledger-${row.object_id}`">
            <td>{{ row.receipt_id }}</td>
            <td>{{ row.path }}</td>
            <td>{{ row.object_id }}</td>
            <td>{{ row.origin_snapshot_id }}</td>
            <td :data-released="String(row.actually_released)">
              {{ row.actually_released ? '已释放' : '未释放（保留）' }}
            </td>
            <td>{{ row.decided_at }}</td>
            <td>{{ row.operator }}</td>
            <td>{{ row.reason }}</td>
          </tr>
          <tr v-if="!ledger.length">
            <td colspan="8" class="empty-state">暂无处置记录，先执行清理</td>
          </tr>
        </tbody>
      </table>

      <h3>执行回执</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>回执编号</th>
            <th>状态</th>
            <th>开始时间</th>
            <th>完成时间</th>
            <th>真实释放</th>
            <th>跳过保留</th>
            <th>尝试记录</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="receipt in receipts" :key="receipt.receipt_id">
            <td>{{ receipt.receipt_id }}</td>
            <td>{{ receipt.status }}</td>
            <td>{{ receipt.started_at }}</td>
            <td>{{ receipt.finished_at ?? '—' }}</td>
            <td>{{ receipt.released_count }}</td>
            <td>{{ receipt.skipped_count }}</td>
            <td>{{ receipt.attempts.map((a) => `${a.action}@${a.at}`).join('；') }}</td>
          </tr>
        </tbody>
      </table>

      <div class="page-actions">
        <button class="btn" type="button" @click="loadNextScope">回查下一次复核范围</button>
        <button class="btn" type="button" @click="loadReadable">核对 S3 快照可读性</button>
      </div>
      <pre v-if="inspectOutput" class="inspect-box">{{ inspectOutput }}</pre>
    </template>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  checkReadable,
  getNextScope,
  getReview,
  listLedger,
  listReceipts,
  listReviews,
  runVacuum,
  type LedgerRow,
  type Receipt,
  type Review,
  type VacuumResult,
} from '@/api/lakehouse'

const route = useRoute()
const reviews = ref<Review[]>([])
const currentReview = ref<Review | null>(null)
const selectedReviewId = ref('')
const operator = ref('值班员甲')
const failAfter = ref<number | null>(null)
const running = ref(false)
const message = ref('')
const lastResult = ref<VacuumResult | null>(null)
const ledger = ref<LedgerRow[]>([])
const receipts = ref<Receipt[]>([])
const inspectOutput = ref('')

function liveRef(objectId: string): string[] {
  const refInfo = currentReview.value?.live_reference?.find((item) => item.object_id === objectId)
  return refInfo?.referring_snapshots ?? []
}

function liveReleased(objectId: string): boolean {
  const refInfo = currentReview.value?.live_reference?.find((item) => item.object_id === objectId)
  return refInfo?.released ?? false
}

async function loadReviews() {
  reviews.value = await listReviews()
  const queryReview = typeof route.query.review === 'string' ? route.query.review : ''
  if (queryReview && reviews.value.some((item) => item.review_id === queryReview)) {
    selectedReviewId.value = queryReview
    await loadReview()
  }
}

async function loadReview() {
  if (!selectedReviewId.value) return
  message.value = ''
  inspectOutput.value = ''
  currentReview.value = await getReview(selectedReviewId.value)
  await refreshExecutionData()
}

async function refreshExecutionData() {
  if (!selectedReviewId.value) return
  const [review, ledgerRows, receiptRows] = await Promise.all([
    getReview(selectedReviewId.value),
    listLedger(selectedReviewId.value),
    listReceipts(selectedReviewId.value),
  ])
  currentReview.value = review
  ledger.value = ledgerRows
  receipts.value = receiptRows
}

async function execute(simulateInterrupt: boolean) {
  if (!currentReview.value) return
  running.value = true
  message.value = ''
  try {
    const result = await runVacuum(
      currentReview.value.review_id,
      operator.value,
      simulateInterrupt ? failAfter.value ?? 1 : null,
    )
    lastResult.value = result
    if (result.interrupted) {
      message.value = `${result.message}（已落盘处置记录保留，可直接重试接续）`
    } else {
      message.value = `清理完成：真实释放 ${result.receipt.released_count} 件，跳过保留 ${result.receipt.skipped_count} 件`
      if (result.receipt.idempotent_replay) {
        message.value = `该复核单已完成过清理，本次为幂等回放，未重复释放任何对象`
      }
    }
    await refreshExecutionData()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '清理执行失败'
  } finally {
    running.value = false
  }
}

async function retryFromInterrupt() {
  if (!currentReview.value) return
  running.value = true
  message.value = ''
  try {
    const result = await runVacuum(currentReview.value.review_id, operator.value, null)
    lastResult.value = result
    message.value = result.interrupted
      ? `仍处于中断：${result.message}`
      : `重试完成：真实释放 ${result.receipt.released_count} 件，跳过保留 ${result.receipt.skipped_count} 件`
    await refreshExecutionData()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '重试失败'
  } finally {
    running.value = false
  }
}

async function duplicateSubmit() {
  await execute(false)
}

async function loadNextScope() {
  if (!currentReview.value) return
  const scope = await getNextScope(currentReview.value.review_id)
  inspectOutput.value = JSON.stringify(scope, null, 2)
}

async function loadReadable() {
  const report = await checkReadable('S3')
  inspectOutput.value = JSON.stringify(report, null, 2)
}

onMounted(async () => {
  try {
    // 若无任何复核单（例如服务刚启动且尚未生成差异），给出友好提示
    await loadReviews()
    if (!reviews.value.length) {
      message.value = '当前还没有回收复核单，请先到数据文件差异视图选择两版快照生成差异并发起复核'
    }
  } catch (error) {
    message.value = error instanceof Error ? error.message : '复核单加载失败'
  }
})
</script>
