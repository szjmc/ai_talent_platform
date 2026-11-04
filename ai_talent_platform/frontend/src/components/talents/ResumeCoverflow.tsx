import { useEffect, useRef, useState } from 'react';
import { Button, Empty, Modal } from 'antd';
import { LeftOutlined, RightOutlined, DragOutlined, ExpandOutlined } from '@ant-design/icons';
import { motion, useReducedMotion } from 'framer-motion';
import type { Talent } from '../../types';
import ResumeDocument from './ResumeDocument';

interface Props {
  talents: Talent[];
  province?: string;
}

/** 克制的弱透视简历 CoverFlow：仅使用 transform/opacity，避免布局抖动。 */
export default function ResumeCoverflow({ talents, province }: Props) {
  const [active, setActive] = useState(0);
  const [preview, setPreview] = useState<Talent | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const wheelTime = useRef(0);
  const dragged = useRef(false);
  const reduceMotion = useReducedMotion();

  useEffect(() => { setActive(0); }, [talents, province]);
  const move = (direction: number) => setActive((current) => Math.max(0, Math.min(talents.length - 1, current + direction)));

  // 原生非 passive 监听用于阻止轮播区域的页面滚动，滚轮与触控板均可切换。
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const onWheel = (event: WheelEvent) => {
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      if (Math.abs(delta) < 12 || talents.length < 2) return;
      event.preventDefault();
      const now = Date.now();
      if (now - wheelTime.current < 280) return;
      wheelTime.current = now;
      move(delta > 0 ? 1 : -1);
    };
    viewport.addEventListener('wheel', onWheel, { passive: false });
    return () => viewport.removeEventListener('wheel', onWheel);
  }, [talents.length]);

  const spring = reduceMotion ? { duration: 0.15 } : { type: 'spring' as const, stiffness: 250, damping: 28, mass: 0.85 };

  return (
    <section className="card resume-coverflow-card">
      <div className="coverflow-header">
        <div><h3 className="section-card-title">人才简历浏览</h3><p>数据库人才档案，点击卡片查看完整简历</p></div>
        <div className="flex items-center gap-3">
          {province && <span className="province-filter-label">已筛选：{province}</span>}
          <span className="coverflow-count"><b>{talents.length ? active + 1 : 0}</b> / {talents.length}</span>
          <Button size="small" icon={<LeftOutlined />} disabled={active === 0} onClick={() => move(-1)} aria-label="上一份简历" />
          <Button size="small" icon={<RightOutlined />} disabled={active >= talents.length - 1} onClick={() => move(1)} aria-label="下一份简历" />
        </div>
      </div>
      {talents.length === 0 ? <Empty className="py-16" description="当前筛选条件下暂无人才简历" /> : <>
        <motion.div
          ref={viewportRef}
          className="coverflow-viewport"
          drag={talents.length > 1 ? 'x' : false}
          dragConstraints={{ left: 0, right: 0 }}
          dragElastic={0.08}
          onDragStart={() => { dragged.current = true; }}
          onDragEnd={(_, info) => {
            if (Math.abs(info.offset.x) > 48 || Math.abs(info.velocity.x) > 420) move(info.offset.x < 0 ? 1 : -1);
            window.setTimeout(() => { dragged.current = false; }, 80);
          }}
        >
          <div className="coverflow-stage">
            {talents.map((talent, index) => {
              const offset = index - active;
              const distance = Math.abs(offset);
              const hidden = distance > 3;
              const scale = Math.max(0.8, 1 - distance * 0.075);
              return (
                <motion.button
                  type="button"
                  key={talent.id}
                  className={`resume-cover-card ${offset === 0 ? 'is-active' : ''}`}
                  style={{ zIndex: 20 - distance, pointerEvents: hidden ? 'none' : 'auto' }}
                  initial={false}
                  animate={{
                    x: offset * 280,
                    y: distance * 9,
                    z: -distance * 70,
                    rotateY: offset * -6,
                    scale,
                    opacity: hidden ? 0 : Math.max(0.32, 1 - distance * 0.19),
                  }}
                  whileHover={{ y: distance * 7 - 5, scale: scale + 0.012 }}
                  transition={spring}
                  onClick={() => { if (!dragged.current) { setActive(index); setPreview(talent); } }}
                  aria-label={`查看${talent.name}的完整简历`}
                >
                  <div className="resume-cover-preview"><ResumeDocument talent={talent} /></div>
                  <div className="resume-cover-caption">
                    <div className="min-w-0 text-left"><strong>{talent.name}<span>{talent.potential_level} 级人才</span></strong><p>{talent.current_position}</p><small>{talent.job_level || '专业序列'} · {talent.work_city || talent.province || talent.country || '地域未填写'}</small></div>
                    <ExpandOutlined />
                  </div>
                </motion.button>
              );
            })}
          </div>
        </motion.div>
        <div className="coverflow-footer">
          <span><DragOutlined />拖拽或使用鼠标滚轮切换</span>
          <div className="coverflow-dots">{talents.map((talent, index) => <button type="button" key={talent.id} aria-label={`切换到第 ${index + 1} 份简历`} className={index === active ? 'active' : ''} onClick={() => setActive(index)} />)}</div>
          <span>弱透视模式 · 流畅浏览</span>
        </div>
      </>}
      <Modal open={!!preview} onCancel={() => setPreview(null)} footer={null} width={760} centered title={preview ? `${preview.name} · 完整简历` : '简历预览'} className="resume-preview-modal">
        {preview && <ResumeDocument talent={preview} detailed />}
      </Modal>
    </section>
  );
}
