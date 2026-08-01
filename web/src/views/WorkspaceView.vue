<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getHealth } from '../api/client'
import AppShell from '../components/AppShell.vue'
import { logger } from '../logging/logger'

const health = ref('loading')

async function refreshHealth(): Promise<void> {
  try {
    const result = await getHealth()
    health.value = result.status
  } catch (error) {
    health.value = 'unavailable'
    logger.error('ui.error', 'Health request failed', {
      reason: error instanceof Error ? error.message : 'unknown',
    })
  }
}

onMounted(refreshHealth)
</script>

<template>
  <AppShell :health="health" />
</template>

