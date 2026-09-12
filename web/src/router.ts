import { createRouter, createWebHistory } from 'vue-router'
import WorkspaceView from './views/WorkspaceView.vue'
import BacktestWorkspaceView from './views/BacktestWorkspaceView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'workspace', component: WorkspaceView },
    { path: '/backtest', name: 'backtest-workspace', component: BacktestWorkspaceView },
  ],
})

