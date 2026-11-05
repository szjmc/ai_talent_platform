import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Empty, message } from 'antd';
import { AuditOutlined, CloseOutlined, DragOutlined, ExpandOutlined, FileTextOutlined, LeftOutlined, RightOutlined, ScheduleOutlined, ShrinkOutlined } from '@ant-design/icons';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import type { Talent } from '../../types';
import ResumeDocument from './ResumeDocument';

interface Props { talents: Talent[]; province?: string; fullscreen: boolean; onFullscreenChange: (open: boolean) => void }

/** 纸质档案 CoverFlow：deck 与 picked 共用卡片实体，支持多卡沿原轨迹取出和放回。 */
export default function ResumeCoverflow({ talents, province, fullscreen, onFullscreenChange }: Props) {
  const [active, setActive] = useState(0);
  const [picked, setPicked] = useState<number[]>([]);
  const [closing, setClosing] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);
  const wheelTime = useRef(0);
  const dragged = useRef(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => { setActive(0); setPicked([]); }, [talents, province]);
  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current); }, []);
  const move = useCallback((direction: number) => setActive(current => Math.max(0, Math.min(talents.length - 1, current + direction))), [talents.length]);
  const putBack = useCallback((id: number) => setPicked(current => current.filter(item => item !== id)), []);
  const closeFullscreen = useCallback(() => {
    if (closing) return;
    setClosing(true); setPicked([]);
    closeTimer.current = setTimeout(() => { onFullscreenChange(false); setClosing(false); }, reduceMotion ? 80 : 520);
  }, [closing, onFullscreenChange, reduceMotion]);
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') closeFullscreen(); };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, [fullscreen, closeFullscreen]);

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

  const pickCard = (talent: Talent, index: number) => {
    if (dragged.current) return;
    setActive(index);
    setPicked(current => current.includes(talent.id) ? [...current.filter(id => id !== talent.id), talent.id] : [...current, talent.id]);
  };
  const invite = (type: string, talent: Talent) => message.success(`演示交互：已为 ${talent.name} 创建${type}`);
  const spring = reduceMotion ? { duration: 0.12 } : { type: 'spring' as const, stiffness: 235, damping: 29, mass: 0.82 };

  return <motion.section layout className={`resume-coverflow-card ${fullscreen ? 'is-fullscreen' : ''} ${closing ? 'is-returning' : ''}`} animate={closing ? { scale: .975, opacity: .7 } : { scale: 1, opacity: 1 }} transition={{ duration: reduceMotion ? .05 : .35 }}>
    <div className="coverflow-header">
      <div><h3 className="section-card-title">{fullscreen ? '全屏人才档案库' : '3D 人才档案卡组'}</h3><p>点击档案上浮取出，可同时查阅多位人才</p></div>
      <div className="coverflow-toolbar">
        {province && <span className="province-filter-label">已筛选：{province}</span>}
        {picked.length > 0 && <span className="picked-counter">已取出 {picked.length} 份</span>}
        <span className="coverflow-count"><b>{talents.length ? active + 1 : 0}</b> / {talents.length}</span>
        <Button size="small" icon={<LeftOutlined />} disabled={active === 0} onClick={() => move(-1)} aria-label="上一份档案" />
        <Button size="small" icon={<RightOutlined />} disabled={active >= talents.length - 1} onClick={() => move(1)} aria-label="下一份档案" />
        {fullscreen ? <button className="archive-global-close" onClick={closeFullscreen} aria-label="放回全部档案并退出全屏"><CloseOutlined /><span>放回全部并退出</span></button> : <Button size="small" icon={<ExpandOutlined />} onClick={() => onFullscreenChange(true)}>全屏卡组</Button>}
      </div>
    </div>
    {talents.length === 0 ? <Empty className="py-16" description="当前筛选条件下暂无人才档案" /> : <>
      <motion.div ref={viewportRef} className="coverflow-viewport" drag={talents.length > 1 ? 'x' : false} dragConstraints={{ left: 0, right: 0 }} dragElastic={0.06}
        onDragStart={() => { dragged.current = true; }} onDragEnd={(_, info) => { if (Math.abs(info.offset.x) > 52 || Math.abs(info.velocity.x) > 430) move(info.offset.x < 0 ? 1 : -1); setTimeout(() => { dragged.current = false; }, 90); }}>
        <div className="coverflow-stage">
          <AnimatePresence>
            {talents.map((talent, index) => {
              const offset = index - active; const distance = Math.abs(offset); const hidden = distance > 3;
              const pickedIndex = picked.indexOf(talent.id); const isPicked = pickedIndex >= 0;
              const spread = Math.min(fullscreen ? 420 : 385, 1050 / Math.max(1, picked.length));
              const floatX = (pickedIndex - (picked.length - 1) / 2) * spread;
              const deckScale = Math.max(.76, 1 - distance * .072);
              return <motion.div layout key={talent.id} className={`resume-cover-card paper-archive-card ${offset === 0 ? 'is-active' : ''} ${isPicked ? 'is-picked' : ''}`}
                style={{ zIndex: isPicked ? 100 + pickedIndex : 20 - distance, pointerEvents: hidden && !isPicked ? 'none' : 'auto' }}
                initial={false} exit={{ opacity: 0, scale: .85 }}
                animate={isPicked ? { x: floatX, y: fullscreen ? -18 : 0, z: 320 + pickedIndex * 8, rotateY: 0, rotateZ: 0, scale: fullscreen ? 1.04 : .96, opacity: 1 } : { x: offset * (fullscreen ? 270 : 245), y: distance * 13, z: -distance * 105, rotateY: offset * -7, rotateZ: offset * .45, scale: deckScale, opacity: hidden ? 0 : Math.max(.28, 1 - distance * .18) }}
                whileHover={isPicked ? undefined : { y: distance * 13 - 5 }} transition={spring}>
                <div className="paper-sheet paper-sheet-back" /><div className="paper-sheet paper-sheet-middle" />
                <div className="archive-card-face" role="button" tabIndex={0} onClick={() => pickCard(talent, index)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') pickCard(talent, index); }} aria-label={`取出${talent.name}的人才档案`}>
                  <div className="archive-paper-tab">TALENT · {String(talent.id).padStart(4, '0')}</div>
                  <div className="resume-cover-preview"><ResumeDocument talent={talent} /></div>
                  <div className="resume-cover-caption"><div className="min-w-0 text-left"><strong>{talent.name}<span>{talent.potential_level} 级人才</span></strong><p>{talent.current_position}</p><small>{talent.job_level || '专业序列'} · {talent.work_city || talent.province || '地域未填写'}</small></div><ExpandOutlined /></div>
                </div>
                {isPicked && <>
                  <button className="picked-card-close" onClick={event => { event.stopPropagation(); putBack(talent.id); }} aria-label={`放回${talent.name}的档案`}><CloseOutlined /></button>
                  <div className="picked-card-actions" onClick={event => event.stopPropagation()}><span>人才邀约</span><Button icon={<ScheduleOutlined />} onClick={() => invite('面试邀约', talent)}>面试邀约</Button><Button icon={<FileTextOutlined />} onClick={() => invite('笔试邀约', talent)}>笔试邀约</Button><Button icon={<AuditOutlined />} onClick={() => invite('测评邀约', talent)}>测评邀约</Button></div>
                </>}
              </motion.div>;
            })}
          </AnimatePresence>
        </div>
      </motion.div>
      <div className="coverflow-footer"><span><DragOutlined />拖拽或滚轮切换 · 自动吸附居中</span><div className="coverflow-dots">{talents.map((talent, index) => <button type="button" key={talent.id} aria-label={`切换到第 ${index + 1} 份档案`} className={index === active ? 'active' : ''} onClick={() => setActive(index)} />)}</div><span>{fullscreen ? <><ShrinkOutlined /> ESC 放回并退出</> : '纸质档案 · 多层 Z 轴空间'}</span></div>
    </>}
  </motion.section>;
}
