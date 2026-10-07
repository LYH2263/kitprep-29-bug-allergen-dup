<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const rows = ref<any[]>([])
const stats = ref<any>({})
const generated = ref(false)
onMounted(async () => {
  const res = await api('/prep/shortages?order_id=1')
  rows.value = res.shortages; stats.value = res.stats; generated.value = !!res.generated
})
</script>
<template>
  <h1>缺料便利贴</h1>
  <p class="sub">主缺料贴：shortage = need − stock（仅主贴正数）；含敏料走专册，不算结存不够</p>
  <p v-if="!generated" class="badge badge-warn" style="margin-bottom:0.8rem">
    备料单尚未正式落库，主缺料贴为空 —— 请先到备料工作台或订单页点「生成备料单」
  </p>
  <template v-else>
    <div class="kp-shortage-sticky" style="max-width:360px;transform:rotate(-1deg);margin-bottom:1rem">
      <h2>⚠ 主缺料 {{ stats.shortage_count }} · 合计 {{ stats.total_shortage_qty }}</h2>
      <div v-for="r in rows" :key="r.ingredient_id" class="kp-shortage-item">
        <span>{{ r.ingredient_name }}</span>
        <span class="kp-qty">−{{ r.shortage }} {{ r.unit }}</span>
      </div>
      <p v-if="!rows.length" style="font-size:0.8rem;margin:0.5rem 0 0">主贴暂无缺料</p>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>原料</th><th>需求</th><th>结存</th><th>缺料</th><th>单位</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.ingredient_id">
            <td>{{ r.ingredient_name }}</td><td>{{ r.need_qty }}</td><td>{{ r.stock_qty }}</td>
            <td><span class="badge badge-bad">{{ r.shortage }}</span></td><td>{{ r.unit }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>
</template>
