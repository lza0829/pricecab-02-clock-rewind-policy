<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const clock = ref('')
const clockInfo = ref(null)
const policyOptions = ref([])
const policy = ref('')
const batches = ref([])
const events = ref([])
const msg = ref('')
const err = ref('')
const result = ref(null)

const isRewind = computed(() => {
  if (!clockInfo.value?.business_clock || !clock.value) return false
  return clock.value < clockInfo.value.business_clock
})

const policyLabel = computed(
  () => policyOptions.value.find((p) => p.value === policy.value)?.label || '',
)

async function refresh() {
  const c = await api.clock()
  clockInfo.value = c
  clock.value = c.business_clock || ''
  policy.value = c.clock_rewind_policy
  const opts = await api.rewindPolicyOptions()
  policyOptions.value = Object.entries(opts.labels).map(([value, label]) => ({ value, label }))
  batches.value = await api.batches()
  events.value = await api.clockEvents().then((r) => r.events)
}

async function savePolicy() {
  msg.value = ''
  err.value = ''
  try {
    await api.setRewindPolicy(policy.value)
    msg.value = '回拨策略已更新（全局唯一、互斥）'
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function saveClock() {
  msg.value = ''
  err.value = ''
  result.value = null
  try {
    const r = await api.setClock(clock.value)
    if (r.rewind) {
      const t = r.rewind
      msg.value = `回拨完成：作废并回退 ${t.rescheduled_tickets.length} 张改价单为可再调度，价签本已按历史恢复`
    } else {
      msg.value = '业务钟已更新'
    }
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function applyDue() {
  msg.value = ''
  err.value = ''
  result.value = null
  try {
    result.value = await api.applyDue()
    msg.value = result.value.applied
      ? `已应用 ${result.value.ticket_count} 张改价单`
      : result.value.message
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

function batchTagClass(status) {
  if (status === 'done') return 'ok'
  if (status === 'running') return 'warn'
  return 'bad'
}

onMounted(() => refresh().catch((e) => (err.value = e.message)))
</script>

<template>
  <section class="hero">
    <h1>生效应用</h1>
    <p>拨动业务钟后，点「应用到期」将 effective_at ≤ 业务钟的改价单写入现行价签。回拨受全局唯一策略约束，应用到期进行中禁止改钟。</p>
  </section>

  <div class="panel">
    <h2>回拨策略（全局唯一，两种互斥）</h2>
    <div class="row">
      <div class="field" style="flex:1">
        <label>当前策略</label>
        <select v-model="policy">
          <option v-for="p in policyOptions" :key="p.value" :value="p.value">{{ p.label }}</option>
        </select>
      </div>
      <button class="btn ghost" type="button" @click="savePolicy">保存策略</button>
    </div>
    <p class="msg" style="margin-top:0">允许回拨时：effective_at 晚于新钟点的已应用改价单统一改回「可再调度」态，价签本按价格历史恢复，历史只追加不删除。</p>
  </div>

  <div class="panel">
    <h2>业务钟</h2>
    <div class="row">
      <div class="field" style="flex:1">
        <label>业务钟 (UTC)</label>
        <input v-model="clock" />
      </div>
      <button class="btn ghost" type="button" @click="saveClock">保存业务钟</button>
      <button class="btn warn" type="button" @click="applyDue">应用到期</button>
    </div>
    <p v-if="isRewind" class="msg err" style="margin-top:0">
      新钟点早于当前业务钟（{{ clockInfo?.business_clock }}），属于回拨，将按「{{ policyLabel }}」处理。
    </p>
    <p v-if="msg" class="msg ok">{{ msg }}</p>
    <p v-if="err" class="msg err">{{ err }}</p>
    <pre v-if="result" style="margin-top:12px;font-size:0.82rem;color:var(--muted);white-space:pre-wrap">{{ JSON.stringify(result, null, 2) }}</pre>
  </div>

  <div class="panel">
    <h2>应用批次</h2>
    <table>
      <thead>
        <tr><th>批次</th><th>业务钟</th><th>改价单数</th><th>明细行</th><th>状态</th><th>说明</th><th>时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="b in batches" :key="b.id">
          <td>{{ b.batch_no }}</td>
          <td>{{ b.clock_at }}</td>
          <td>{{ b.ticket_count }}</td>
          <td>{{ b.line_count }}</td>
          <td><span class="tag" :class="batchTagClass(b.status)">{{ b.status }}</span></td>
          <td>{{ b.note || '—' }}</td>
          <td>{{ b.created_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="panel">
    <h2>业务钟留痕</h2>
    <table>
      <thead>
        <tr><th>类型</th><th>原钟点</th><th>新钟点</th><th>策略</th><th>说明</th><th>时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="e in events" :key="e.id">
          <td><span class="tag" :class="e.event_type === 'clock_rewind' ? 'bad' : 'muted'">{{ e.event_type }}</span></td>
          <td>{{ e.from_clock || '—' }}</td>
          <td>{{ e.to_clock }}</td>
          <td>{{ e.policy }}</td>
          <td style="max-width:360px">{{ JSON.stringify(e.detail) }}</td>
          <td>{{ e.created_at }}</td>
        </tr>
        <tr v-if="!events.length"><td colspan="6" class="msg">暂无拨动记录</td></tr>
      </tbody>
    </table>
  </div>
</template>
