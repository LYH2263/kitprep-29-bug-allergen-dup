<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const rows = ref<any[]>([])
const savingId = ref<number | null>(null)
const savedId = ref<number | null>(null)
const err = ref('')

async function load() { rows.value = await api('/inventory') }
async function toggleAllergen(r: any) {
  savingId.value = r.id; err.value = ''; savedId.value = null
  try {
    const updated = await api('/inventory/' + r.id, {
      method: 'PATCH',
      body: JSON.stringify({ is_allergen: r.is_allergen }),
    })
    r.is_allergen = updated.is_allergen
    savedId.value = r.id
  } catch (e: any) {
    err.value = '含敏标记保存失败：' + (e?.message || e)
    r.is_allergen = !r.is_allergen
  } finally {
    savingId.value = null
  }
}
onMounted(load)
</script>
<template>
  <h1>库存</h1>
  <p class="sub">中央厨房原料库存 · 结存为实物账，备料生成只记占用列、不扣结存</p>
  <div class="card">
    <table>
      <thead>
        <tr><th>编码</th><th>名称</th><th>结存</th><th>已占用</th><th>单位</th><th>含敏</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id ?? JSON.stringify(r)">
          <td>{{ r.code }}</td>
          <td>{{ r.name }}</td>
          <td>{{ r.stock_qty }}</td>
          <td>{{ r.occupied_qty }}</td>
          <td>{{ r.unit }}</td>
          <td>
            <label style="display:inline-flex;align-items:center;gap:0.3rem;cursor:pointer">
              <input type="checkbox" :checked="r.is_allergen"
                     :disabled="savingId === r.id"
                     @change="r.is_allergen = ($event.target as HTMLInputElement).checked; toggleAllergen(r)" />
              <span :class="r.is_allergen ? 'badge badge-warn' : 'muted'" style="font-size:0.72rem">
                {{ r.is_allergen ? '含敏' : '普通' }}
              </span>
              <span v-if="savingId === r.id" class="muted" style="font-size:0.7rem">保存中…</span>
              <span v-else-if="savedId === r.id" class="badge badge-ok" style="font-size:0.7rem">已保存</span>
            </label>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="err" class="badge badge-bad" style="margin-top:0.6rem">{{ err }}</p>
    <p class="muted" style="font-size:0.75rem;margin:0.6rem 0 0">
      勾选含敏并保存后，下一次生成备料单时该原料只进敏料专册；已落下的旧单不随新标记改字。
    </p>
  </div>
</template>
