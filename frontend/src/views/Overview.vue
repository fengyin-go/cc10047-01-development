<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "流数据源", "created": 0, "pending": 0, "abnormal": 0}, {"name": "湖表定义", "created": 0, "pending": 0, "abnormal": 0}, {"name": "流式摄入", "created": 0, "pending": 0, "abnormal": 0}, {"name": "提交版本", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分区合并", "created": 0, "pending": 0, "abnormal": 0}, {"name": "流Schema演进", "created": 0, "pending": 0, "abnormal": 0}, {"name": "快照管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "流质量校验", "created": 0, "pending": 0, "abnormal": 0}, {"name": "历史清理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "批流合并", "created": 0, "pending": 0, "abnormal": 0}, {"name": "湖表血缘", "created": 0, "pending": 0, "abnormal": 0}, {"name": "保留策略", "created": 0, "pending": 0, "abnormal": 0}, {"name": "压缩任务", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据回填", "created": 0, "pending": 0, "abnormal": 0}, {"name": "流处理指标", "created": 0, "pending": 0, "abnormal": 0}, {"name": "格式迁移", "created": 0, "pending": 0, "abnormal": 0}, {"name": "表目录同步", "created": 0, "pending": 0, "abnormal": 0}, {"name": "湖仓审计", "created": 0, "pending": 0, "abnormal": 0}]
  }
})
</script>
