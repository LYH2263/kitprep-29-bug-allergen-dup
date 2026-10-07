<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const orders = ref<any[]>([])
const lines = ref<any[]>([])
const prepState = ref<Record<number, any>>({})
const busyId = ref<number | null>(null)
const err = ref('')

async function loadPrepState(orderId: number) {
  try { prepState.value[orderId] = await api('/prep/latest?order_id=' + orderId) }
  catch { /* 未生成 */ }
}
async function generate(orderId: number) {
  if (busyId.value) return
  busyId.value = orderId; err.value = ''
  try {
    // 与备料台同一个端点、同一套两本账：已落库则原样返回，抢点也不会产生第二套
    prepState.value[orderId] = await api('/prep/run?order_id=' + orderId, { method: 'POST' })
  } catch (e: any) {
    err.value = '生成失败，两本账整次退回：' + (e?.message || e)
  } finally {
    busyId.value = null
  }
}
onMounted(async () => {
  orders.value = await api('/orders')
  if (orders.value.length) {
    lines.value = await api('/orders/' + orders.value[0].id + '/lines')
    for (const o of orders.value) await loadPrepState(o.id)
  }
})
</script>
<template>
  <h1>订单芯片</h1>
  <p class="sub">门店要货 · 在此生成与备料台共用同一套主贴+专册账</p>
  <div class="kp-chips" style="margin-bottom:1rem">
    <span v-for="o in orders" :key="o.id" class="kp-chip">{{ o.code }} · {{ o.outlet }} · {{ o.status }}</span>
  </div>
  <p v-if="err" class="badge badge-bad" style="margin-bottom:0.8rem">{{ err }}</p>
  <div class="card" style="max-width:720px">
    <table>
      <thead><tr><th>订单</th><th>门店</th><th>备料账状态</th><th></th></tr></thead>
      <tbody>
        <tr v-for="o in orders" :key="o.id">
          <td>{{ o.code }}</td><td>{{ o.outlet }}</td>
          <td>
            <span v-if="prepState[o.id]?.immutable" class="badge badge-ok">
              已落库 #{{ prepState[o.id].id }}（主贴 {{ prepState[o.id].stats?.ingredient_count ?? 0 }}
              / 专册 {{ prepState[o.id].allergen_stats?.ingredient_count ?? 0 }}，不可改字）
            </span>
            <span v-else-if="prepState[o.id]" class="badge badge-warn">未落库（仅试算）</span>
            <span v-else class="muted">—</span>
          </td>
          <td>
            <button class="btn" :disabled="busyId === o.id || prepState[o.id]?.immutable"
                    @click="generate(o.id)">
              {{ busyId === o.id ? '生成中…' : (prepState[o.id]?.immutable ? '已是同一套账' : '生成备料单') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="kp-worksheet" v-if="lines.length" style="max-width:720px">
    <h2>订单行 · {{ orders[0]?.code }}</h2>
    <table>
      <thead><tr><th>菜品</th><th>份数</th></tr></thead>
      <tbody>
        <tr v-for="l in lines" :key="l.id"><td>{{ l.dish_name }}</td><td>{{ l.portions }}</td></tr>
      </tbody>
    </table>
  </div>
</template>
