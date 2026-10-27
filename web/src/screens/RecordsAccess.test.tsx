import { screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from '../App';
import { fakeFetch, renderWithClient, SESSION_DEV } from '../test/helpers';
import { RECORDS } from '../test/records';
import { SettingsScreen } from './SettingsScreen';
import type { AuthSession } from '../api/types';

afterEach(() => { window.location.hash = ''; vi.unstubAllGlobals(); });
it('the app owns the records hash and shows its records navigation item selected', async () => {
  window.location.hash = '#/records';
  fakeFetch({ '/api/auth/session': { json: SESSION_DEV }, '/api/workspace/experiments': { json: { experiments: RECORDS } } });
  renderWithClient(<App />);
  expect(await screen.findByTestId('records-screen')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '기록' })).toHaveAttribute('aria-current', 'page');
});
it('settings keeps an explicit legacy path for controls that have not moved and an admin metrics entry', async () => {
  fakeFetch({ '/api/experimenter/settings': { json: { settings: {} } }, '/api/voice/voices': { json: { voices: [] } } });
  renderWithClient(<SettingsScreen session={{ ...SESSION_DEV, account: { ...SESSION_DEV.account, admin: true } } as AuthSession} />);
  expect(await screen.findByRole('link', { name: '이전 화면의 설정 열기' })).toHaveAttribute('href', '/index.html?legacy=1#/settings');
  expect(screen.getByRole('link', { name: '연구실 지표 보기' })).toHaveAttribute('href', '/admin-metrics.html');
});
