<template>
  <section class="page" data-module="snapshot_mgr">
    <header class="page-head">
      <div>
        <h2>数据文件差异视图</h2>
        <p class="page-desc">
          选择同一湖表的两版快照，按物理文件计算差异；移除、改写、补进三类文件分开列示，
          视图生成后固化，后续快照新增引用不会回改原比较。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/vacuum_review">前往回收复核与历史清理</RouterLink>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="buildDiff">
      <label class="filter-item">
        <span>基准快照</span>
        <select v-model="baseId" data-testid="diff-base">
          <option v-for="snap in snapshots" :key="snap.snapshot_id" :value="snap.snapshot_id">
            {{ snap.snapshot_id }}（{{ snap.table }}，{{ snap.file_count }} 个文件）
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>目标快照</span>
        <select v-model="targetId" data-testid="diff-target">
          <option v-for="snap in snapshots" :key="snap.snapshot_id" :value="snap.snapshot_id">
            {{ snap.snapshot_id }}（{{ snap.table }}，{{ snap.file_count }} 个文件）
          </option>
        </select>
      </label>
      <button class="btn primary" data-testid="diff-run" type="submit" :disabled="loading">
        {{ loading ? '比较中…' : '计算物理文件差异' }}
      </button>
    </form>

    <p v-if="message" class="error-text">{{ message }}</p>

    <div v-if="currentDiff" class="diff-summary">
      <article class="stat-card">
        <span class="stat-label">移除文件（可发起回收复核）</span>
        <strong class="stat-value">{{ currentDiff.removed_count }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">改写文件（旧对象）</span>
        <strong class="stat-value">{{ currentDiff.rewritten_count }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">补进文件</span>
        <strong class="stat-value">{{ currentDiff.added_count }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">视图状态</span>
        <strong class="stat-value">{{ currentDiff.frozen ? '已固化' : '可变' }}</strong>
      </article>
    </div>

    <div v-for="group in diffGroups" :key="group.key" class="diff-group">
      <h3>{{ group.title }}（{{ group.items.length }}）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>物理路径</th>
            <th>文件身份 object_id</th>
            <th>校验和</th>
            <th>大小(B)</th>
            <th>原快照版本</th>
            <th v-if="group.key === 'rewritten'">改写后新身份</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in group.items" :key="item.object_id">
            <td>{{ item.path }}</td>
            <td>{{ item.object_id }}</td>
            <td>{{ item.checksum }}</td>
            <td>{{ item.size_bytes }}</td>
            <td>{{ item.origin_snapshot_id }}</td>
            <td v-if="group.key === 'rewritten'">{{ item.paired_object_id ?? '—' }}</td>
          </tr>
          <tr v-if="!group.items.length">
            <td :colspan="group.key === 'rewritten' ? 6 : 5" class="empty-state">本类无差异文件</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="currentDiff && (currentDiff.removed_count || currentDiff.rewritten_count)" class="page-actions">
      <button
        class="btn primary"
        data-testid="create-review"
        type="button"
        :disabled="submitting"
        @click="submitReview"
      >
        {{ submitting ? '提交中…' : `从移除项发起回收复核（${currentDiff.removed_count + currentDiff.rewritten_count} 件）` }}
      </button>
      <RouterLink v-if="createdReviewId" class="btn" :to="`/vacuum_review?review=${createdReviewId}`">
        查看复核单 {{ createdReviewId }}
      </RouterLink>
    </div>

    <div v-if="diffs.length" class="diff-history">
      <h3>历史差异视图（不可变）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>差异视图</th>
            <th>湖表</th>
            <th>基准→目标</th>
            <th>移除/改写/补进</th>
            <th>生成时间</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="view in diffs"
            :key="view.diff_id"
            :data-testid="`diff-row-${view.diff_id}`"
            @click="currentDiff = view"
          >
            <td>{{ view.diff_id }}</td>
            <td>{{ view.table }}</td>
            <td>{{ view.base_snapshot_id }} → {{ view.target_snapshot_id }}</td>
            <td>{{ view.removed_count }} / {{ view.rewritten_count }} / {{ view.added_count }}</td>
            <td>{{ view.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createDiff,
  createReview,
  listDiffs,
  listSnapshots,
  type DiffFileItem,
  type DiffView,
  type SnapshotInfo,
} from '@/api/lakehouse'

const snapshots = ref<SnapshotInfo[]>([])
const diffs = ref<DiffView[]>([])
const currentDiff = ref<DiffView | null>(null)
const baseId = ref('S1')
const targetId = ref('S2')
const loading = ref(false)
const submitting = ref(false)
const message = ref('')
const createdReviewId = ref('')

const diffGroups = computed(() => {
  if (!currentDiff.value) return []
  return [
    { key: 'removed', title: '移除文件（基准有、目标无）', items: currentDiff.value.removed as DiffFileItem[] },
    { key: 'rewritten', title: '改写文件（同路径物理对象已替换）', items: currentDiff.value.rewritten as DiffFileItem[] },
    { key: 'added', title: '补进文件（目标新增）', items: currentDiff.value.added as DiffFileItem[] },
  ]
})

async function loadSnapshots() {
  snapshots.value = await listSnapshots()
}

async function loadDiffs() {
  diffs.value = await listDiffs()
}

async function buildDiff() {
  loading.value = true
  message.value = ''
  try {
    currentDiff.value = await createDiff(baseId.value, targetId.value)
    createdReviewId.value = ''
    await loadDiffs()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '差异计算失败'
  } finally {
    loading.value = false
  }
}

async function submitReview() {
  if (!currentDiff.value) return
  submitting.value = true
  message.value = ''
  try {
    const review = await createReview(currentDiff.value.diff_id, '值班员甲')
    createdReviewId.value = review.review_id
    message.value = `回收复核单 ${review.review_id} 已创建，复核范围 ${review.item_count} 件`
  } catch (error) {
    message.value = error instanceof Error ? error.message : '复核发起失败'
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    await Promise.all([loadSnapshots(), loadDiffs()])
  } catch (error) {
    message.value = error instanceof Error ? error.message : '数据加载失败'
  }
})
</script>
