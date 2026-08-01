import { createApp } from 'vue'
import App from './App.vue'
import { logger } from './logging/logger'
import { router } from './router'
import './styles.css'

function bootstrap(): void {
  logger.start()
  logger.info('app.started', 'Vue application started')
  createApp(App).use(router).mount('#app')
}

bootstrap()

