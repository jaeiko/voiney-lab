import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Smile } from 'lucide-react';
import { useState } from 'react';
import { expect, it, vi } from 'vitest';

import { Button, ButtonLink, Checkbox, Field, RatingScale, Select, TextInput } from './Controls';

// Lane UI1: one button, one rating scale, one field for the records and the end of a run.

it('a button names its look and is a button by default; a primary link stays a link', () => {
  const click = vi.fn();
  render(<><Button onClick={click}>저장</Button><Button variant='primary' size='big' icon={Smile}>보내기</Button><ButtonLink variant='quiet' href='#/records'>기록 목록으로</ButtonLink></>);
  const save = screen.getByRole('button', { name: '저장' });
  expect(save).toHaveAttribute('type', 'button');
  expect(save).toHaveClass('btn');
  expect(save).not.toHaveClass('btn-primary');
  expect(screen.getByRole('button', { name: '보내기' })).toHaveClass('btn', 'btn-primary', 'btn-big');
  expect(screen.getByRole('link', { name: '기록 목록으로' })).toHaveClass('btn', 'btn-quiet');
});

function Scale() {
  const [value, setValue] = useState<number | null>(null);
  return <><p id='q'>오늘 안내는 어땠나요?</p><RatingScale value={value} onChange={setValue} labels={['1번', '2번', '3번', '4번', '5번']} labelledBy='q' testId='scale' /></>;
}

it('the 1–5 scale is one radio group: a press or the arrow keys choose one point', async () => {
  render(<Scale />);
  const group = screen.getByRole('radiogroup', { name: '오늘 안내는 어땠나요?' });
  expect(group).toBeInTheDocument();
  await userEvent.click(screen.getByRole('radio', { name: '4번' }));
  expect(screen.getByRole('radio', { name: '4번' })).toHaveAttribute('aria-checked', 'true');
  expect(screen.getByRole('radio', { name: '4번' })).toHaveAttribute('tabindex', '0');
  await userEvent.keyboard('{ArrowRight}');
  expect(screen.getByRole('radio', { name: '5번' })).toHaveAttribute('aria-checked', 'true');
  expect(screen.getByRole('radio', { name: '5번' })).toHaveFocus();
  await userEvent.keyboard('{ArrowRight}');
  expect(screen.getByRole('radio', { name: '5번' })).toHaveAttribute('aria-checked', 'true');
  await userEvent.keyboard('{ArrowLeft}{ArrowLeft}');
  expect(screen.getByRole('radio', { name: '3번' })).toHaveAttribute('aria-checked', 'true');
  expect(screen.getAllByRole('radio').filter(tile => tile.getAttribute('aria-checked') === 'true')).toHaveLength(1);
});

it('a field labels its control; a checkbox line is labelled by its words', async () => {
  const toggle = vi.fn();
  render(<><Field label='상태'><Select defaultValue=''><option value=''>전체</option></Select></Field><Field label='묶음 이름'><TextInput /></Field><Checkbox label='세포 염색 견본' onChange={toggle} /></>);
  expect(screen.getByLabelText('상태')).toHaveClass('field-control', 'field-select');
  expect(screen.getByLabelText('묶음 이름')).toHaveClass('field-control');
  await userEvent.click(screen.getByText('세포 염색 견본'));
  expect(toggle).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('checkbox', { name: '세포 염색 견본' })).toBeChecked();
});
