<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const book = ref([])
const history = ref([])
const err = ref('')

onMounted(async () => {
  try {
    book.value = await api.priceBook()
    history.value = await api.history()
  } catch (e) {
    err.value = e.message
  }
})
</script>

<template>
  <section class="hero">
    <h1>现行价签</h1>
    <p>价签本反映已生效的最新价格；下方为历史生效记录。</p>
  </section>
  <p v-if="err" class="msg err">{{ err }}</p>

  <div class="panel">
    <h2>价签本</h2>
    <table>
      <thead>
        <tr><th>SKU</th><th>品名</th><th>分类</th><th>现行价</th><th>来源单</th><th>生效时刻</th><th>更新</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in book" :key="r.sku_code">
          <td>{{ r.sku_code }}</td>
          <td>{{ r.title }}</td>
          <td>{{ r.category }}</td>
          <td>{{ r.current_price }}</td>
          <td>{{ r.source_ticket_no || '初始' }}</td>
          <td>{{ r.effective_at }}</td>
          <td>{{ r.updated_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="panel">
    <h2>价格历史</h2>
    <p class="msg" style="margin-top:0">历史只追加不删除；回拨后被作废的应用结果标注为 voided 并附作废说明，对账只取 active 记录。</p>
    <table>
      <thead>
        <tr><th>SKU</th><th>价格</th><th>来源单</th><th>生效时刻</th><th>记录时间</th><th>状态</th><th>说明</th></tr>
      </thead>
      <tbody>
        <tr v-for="h in history" :key="h.id" :style="h.status === 'voided' ? { opacity: 0.55 } : {}">
          <td>{{ h.sku_code }}</td>
          <td>{{ h.price }}</td>
          <td>{{ h.source_ticket_no || '初始' }}</td>
          <td>{{ h.effective_at }}</td>
          <td>{{ h.recorded_at }}</td>
          <td><span class="tag" :class="h.status === 'voided' ? 'bad' : 'ok'">{{ h.status }}</span></td>
          <td>{{ h.note || '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
