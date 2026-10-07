<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const tree = ref<any[]>([])
const data = ref<any>(null)
const shortages = ref<any[]>([])
const orders = ref<any[]>([])
const busy = ref(false)
const err = ref('')

async function refreshShortages() {
  try {
    const res = await api('/prep/shortages?order_id=1')
    shortages.value = res.shortages || []
  } catch { shortages.value = [] }
}

async function run() {
  if (busy.value) return
  busy.value = true; err.value = ''
  try {
    // 与订单页同一个端点：抢点生成也只许拿到同一套两本账
    data.value = await api('/prep/run?order_id=1', { method: 'POST' })
    await refreshShortages()
  } catch (e: any) {
    err.value = '生成失败（整次退回，主贴/专册/占用列均未落库）：' + (e?.message || e)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  tree.value = await api('/bom/tree')
  orders.value = await api('/orders')
  // 只读载入已落库的账或按当前标记试算，不隐式生成
  data.value = await api('/prep/latest?order_id=1')
  await refreshShortages()
})
</script>
<template>
  <h1>备料工作台</h1>
  <p class="sub">左 BOM 树 · 中主贴+敏料专册 · 右主缺料贴 · 顶栏订单芯片</p>
  <div class="kp-chips" style="margin-bottom:0.75rem" v-if="orders.length">
    <span v-for="o in orders" :key="o.id" class="kp-chip" style="cursor:default">
      {{ o.code }} · {{ o.outlet }}
    </span>
  </div>
  <button class="btn" :disabled="busy || data?.immutable" @click="run">
    {{ busy ? '生成中…' : (data?.immutable ? '已落库 · 禁止改字' : '生成备料单') }}
  </button>
  <span v-if="data?.immutable" class="badge badge-warn" style="margin-left:0.6rem">
    本单已按当时含敏标记落库，改标记只影响之后新单，旧单不可改
  </span>
  <span v-else-if="data?.generated === false" class="muted" style="margin-left:0.6rem;font-size:0.78rem">
    当前为试算，点按钮才正式落两本账
  </span>
  <p v-if="err" class="badge badge-bad" style="margin-left:0.6rem">{{ err }}</p>

  <div class="kp-workbench" style="margin-top:0.85rem">
    <aside class="kp-bom-tree">
      <h2>菜品 / BOM</h2>
      <div v-for="d in tree" :key="d.code" class="kp-dish-node">
        <strong>{{ d.dish }}</strong>
        <span style="font-size:0.7rem;color:#8a8078">{{ d.code }}</span>
        <ul>
          <li v-for="(c,i) in d.children" :key="i">{{ c.ingredient }} · {{ c.qty }} {{ c.unit }}</li>
        </ul>
      </div>
    </aside>

    <section class="kp-worksheet" v-if="data">
      <h2>主贴（普通料）· {{ data.order?.code }} · {{ data.order?.outlet }}</h2>
      <table>
        <thead><tr><th>原料</th><th>需求/占用</th><th>结存</th><th>单位</th></tr></thead>
        <tbody>
          <tr v-for="l in data.prep_lines" :key="l.ingredient_id">
            <td>{{ l.ingredient_name }}</td><td>{{ l.need_qty }}</td><td>{{ l.stock_qty }}</td><td>{{ l.unit }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!data.prep_lines?.length" class="muted" style="font-size:0.8rem;margin:0.5rem 0 0">主贴为空</p>

      <h2 style="margin-top:0.9rem">敏料专册（含敏料只进此册）</h2>
      <table>
        <thead><tr><th>原料</th><th>需求/占用</th><th>结存</th><th>单位</th><th>含敏</th></tr></thead>
        <tbody>
          <tr v-for="l in data.allergen_lines" :key="l.ingredient_id" style="background:rgba(196,122,44,0.12)">
            <td>{{ l.ingredient_name }}</td><td>{{ l.need_qty }}</td><td>{{ l.stock_qty }}</td>
            <td>{{ l.unit }}</td><td><span class="badge badge-warn">含敏</span></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!data.allergen_lines?.length" class="muted" style="font-size:0.8rem;margin:0.5rem 0 0">
        未打过含敏，专册为空，全部走主贴
      </p>
      <p class="muted" style="font-size:0.72rem;margin:0.6rem 0 0">
        结存列仅展示生成时数字；占用已单列记账，不扣结存。
      </p>
    </section>

    <aside class="kp-shortage-sticky">
      <h2>⚠ 主缺料贴</h2>
      <div v-for="r in shortages" :key="r.ingredient_id" class="kp-shortage-item">
        <span>{{ r.ingredient_name }}</span>
        <span class="kp-qty">−{{ r.shortage }} {{ r.unit }}</span>
      </div>
      <p v-if="!shortages.length" style="font-size:0.8rem;margin:0.5rem 0 0">主贴暂无缺料</p>
      <p style="font-size:0.68rem;margin:0.5rem 0 0;color:#7a6a3e">
        含敏料在专册处理，不计入本贴；拆册不等于结存不够。
      </p>
    </aside>
  </div>
</template>
