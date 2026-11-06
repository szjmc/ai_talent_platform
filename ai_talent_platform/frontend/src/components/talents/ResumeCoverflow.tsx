import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Empty } from 'antd';
import { AuditOutlined, CloseOutlined, DragOutlined, ExpandOutlined, FileTextOutlined, LeftOutlined, RightOutlined, ScheduleOutlined, ShrinkOutlined } from '@ant-design/icons';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import type { Talent } from '../../types';
import ResumeDocument from './ResumeDocument';
import InviteDialog from './InviteDialog';
import type { InviteType } from '../../services/api';

interface Props { talents: Talent[]; province?: string; fullscreen: boolean; onFullscreenChange: (open: boolean) => void }

/**
 * 纸质档案 CoverFlow（[Agent组] 取放交互修订）：
 * - **单卡取出**：同一时间只查阅一位（pickedId 单值），取出的卡片固定居中；
 * - 信息完整：取出状态解除行截断并可滚动查看，各断点卡片高度自洽（见 talents.css 修正段）；
 * - 可取消：卡内 ✕、Esc、再次点击卡面、工具栏「收起档案」四种方式放回；
 * - 无卡顿：去掉 framer layout 测量与 spring 回弹，统一短时长 tween（transform/opacity）。
 */
export default function ResumeCoverflow({ talents, province, fullscreen, onFullscreenChange }: Props) {
  const [active, setActive] = useState(0);
  const [pickedId, setPickedId] = useState<number | null>(null);
  const [closing, setClosing] = useState(false);
  const [inviteTarget, setInviteTarget] = useState<{ talent: Talent; kind: InviteType } | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const wheelTime = useRef(0);
  const dragged = useRef(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => { setActive(0); setPickedId(null); }, [talents, province]);
  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current); }, []);
  const move = useCallback((direction: number) => setActive(current => Math.max(0, Math.min(talents.length - 1, current + direction))), [talents.length]);
  const putBack = useCallback(() => setPickedId(null), []);
  const closeFullscreen = useCallback(() => {
    if (closing) return;
    setClosing(true); setPickedId(null);
    closeTimer.current = setTimeout(() => { onFullscreenChange(false); setClosing(false); }, reduceMotion ? 60 : 260);
  }, [closing, onFullscreenChange, reduceMotion]);

  // Esc：全屏 -> 放回并退出；非全屏且有取出 -> 单卡放回（可取消）
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') closeFullscreen(); };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, [fullscreen, closeFullscreen]);
  useEffect(() => {
    if (fullscreen) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && pickedId !== null) putBack(); };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, [fullscreen, pickedId, putBack]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const onWheel = (event: WheelEvent) => {
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      if (Math.abs(delta) < 12 || talents.length < 2) return;
      event.preventDefault();
      const now = Date.now(); if (now - wheelTime.current < 260) return;
      wheelTime.current = now; move(delta > 0 ? 1 : -1);
    };
    viewport.addEventListener('wheel', onWheel, { passive: false });
    return () => viewport.removeEventListener('wheel', onWheel);
  }, [talents.length, move]);

  /** 单卡取出/放回（点击同一张 = 放回；点另一张 = 切换到那张）。 */
  const pickCard = (talent: Talent, index: number) => {
    if (dragged.current) return;
    setActive(index);
    setPickedId(current => (current === talent.id ? null : talent.id));
  };

  // 统一短 tween：可预期的时长（无弹簧尾迹），reduced-motion 更短
  const transition = reduceMotion ? { duration: 0.08 } : { duration: 0.26, ease: [0.22, 0.61, 0.36, 1] as const };
  const pickedTalent = talents.find(t => t.id === pickedId) ?? null;

  return (
    <section className={`resume-coverflow-card ${fullscreen ? 'is-fullscreen' : ''} ${closing ? 'is-returning' : ''}`}>
      <div className="coverflow-header">
        <div>
          <h3 className="section-card-title">{fullscreen ? '全屏人才档案库' : '3D 人才档案卡组'}</h3>
          <p>点击档案取出查阅，同一时间仅查阅一位（✕ / Esc / 再次点击可放回）</p>
        </div>
        <div className="coverflow-toolbar">
          {province && <span className="province-filter-label">已筛选：{province}</span>}
          {pickedTalent && <span className="picked-counter">正在查阅：{pickedTalent.name}</span>}
          {pickedTalent && <Button size="small" onClick={putBack}>收起档案</Button>}
          <span className="coverflow-count"><b>{talents.length ? active + 1 : 0}</b> / {talents.length}</span>
          <Button size="small" icon={<LeftOutlined />} disabled={active === 0} onClick={() => move(-1)} aria-label="上一份档案" />
          <Button size="small" icon={<RightOutlined />} disabled={active >= talents.length - 1} onClick={() => move(1)} aria-label="下一份档案" />
          {fullscreen
            ? <button className="archive-global-close" onClick={closeFullscreen} aria-label="放回全部档案并退出全屏"><CloseOutlined /><span>放回全部并退出</span></button>
            : <Button size="small" icon={<ExpandOutlined />} onClick={() => onFullscreenChange(true)}>全屏卡组</Button>}
        </div>
      </div>
      {talents.length === 0 ? <Empty className="py-16" description="当前筛选条件下暂无人才档案" /> : <>
        <motion.div
          ref={viewportRef}
          className="coverflow-viewport"
          drag={talents.length > 1 ? 'x' : false}
          dragConstraints={{ left: 0, right: 0 }}
          dragElastic={0.06}
          onDragStart={() => { dragged.current = true; }}
          onDragEnd={(_, info) => {
            if (Math.abs(info.offset.x) > 52 || Math.abs(info.velocity.x) > 430) move(info.offset.x < 0 ? 1 : -1);
            setTimeout(() => { dragged.current = false; }, 90);
          }}
        >
          <div className="coverflow-stage">
            <AnimatePresence>
              {talents.map((talent, index) => {
                const offset = index - active;
                const distance = Math.abs(offset);
                const hidden = distance > 3;
                const isPicked = pickedId === talent.id;
                const deckScale = Math.max(.76, 1 - distance * .072);
                return (
                  <motion.div
                    key={talent.id}
                    className={`resume-cover-card paper-archive-card ${offset === 0 ? 'is-active' : ''} ${isPicked ? 'is-picked' : ''}`}
                    style={{ zIndex: isPicked ? 100 : 20 - distance, pointerEvents: hidden && !isPicked ? 'none' : 'auto' }}
                    initial={false}
                    exit={{ opacity: 0, scale: .85, transition: { duration: .18 } }}
                    animate={
                      isPicked
                        ? { x: 0, y: fullscreen ? -8 : 0, rotateY: 0, rotateZ: 0, scale: 1, opacity: 1 }
                        : {
                            x: offset * (fullscreen ? 285 : 255),
                            y: distance * 13,
                            rotateY: offset * -3.5,
                            rotateZ: offset * .35,
                            scale: deckScale,
                            opacity: hidden ? 0 : pickedTalent ? Math.max(.14, .5 - distance * .12) : Math.max(.42, 1 - distance * .14),
                          }
                    }
                    whileHover={isPicked || pickedTalent ? undefined : { y: distance * 13 - 5 }}
                    transition={transition}
                  >
                    <div className="paper-sheet paper-sheet-back" /><div className="paper-sheet paper-sheet-middle" />
                    <div
                      className="archive-card-face"
                      role="button"
                      tabIndex={0}
                      onClick={() => pickCard(talent, index)}
                      onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pickCard(talent, index); } }}
                      aria-label={isPicked ? `放回${talent.name}的人才档案` : `取出${talent.name}的人才档案`}
                    >
                      <div className="archive-paper-tab">TALENT · {String(talent.id).padStart(4, '0')}</div>
                      <div className="resume-cover-preview"><ResumeDocument talent={talent} /></div>
                      <div className="resume-cover-caption">
                        <div className="min-w-0 text-left">
                          <strong>{talent.name}<span>{talent.potential_level} 级人才</span></strong>
                          <p>{talent.current_position}</p>
                          <small>{talent.job_level || '专业序列'} · {talent.work_city || talent.province || '地域未填写'}</small>
                        </div>
                        <ExpandOutlined />
                      </div>
                    </div>
                    {isPicked && <>
                      <button
                        className="picked-card-close"
                        onClick={event => { event.stopPropagation(); putBack(); }}
                        aria-label={`放回${talent.name}的档案（取消查阅）`}
                      ><CloseOutlined /></button>
                      <div className="picked-card-actions" onClick={event => event.stopPropagation()}>
                        <span>人才邀约</span>
                        <Button icon={<ScheduleOutlined />} onClick={() => setInviteTarget({ talent, kind: 'interview' })}>面试邀约</Button>
                        <Button icon={<FileTextOutlined />} onClick={() => setInviteTarget({ talent, kind: 'written_test' })}>笔试邀约</Button>
                        <Button icon={<AuditOutlined />} onClick={() => setInviteTarget({ talent, kind: 'assessment' })}>测评邀约</Button>
                      </div>
                    </>}
                  </motion.div>
                );
              })}
            </AnimatePresence>
          </div>
        </motion.div>
        <div className="coverflow-footer">
          <span><DragOutlined />拖拽或滚轮切换 · 自动吸附居中</span>
          <div className="coverflow-dots">{talents.map((talent, index) => (
            <button type="button" key={talent.id} aria-label={`切换到第 ${index + 1} 份档案`} className={index === active ? 'active' : ''} onClick={() => setActive(index)} />
          ))}</div>
          <span>{fullscreen ? <><ShrinkOutlined /> ESC 放回并退出</> : pickedId !== null ? <><ShrinkOutlined /> ESC 放回当前档案</> : '纸质档案 · 多层 Z 轴空间'}</span>
        </div>
      </>}
      <InviteDialog
        open={!!inviteTarget}
        talent={inviteTarget?.talent ?? null}
        initialKind={inviteTarget?.kind ?? 'written_test'}
        onClose={() => setInviteTarget(null)}
      />
    </section>
  );
}
