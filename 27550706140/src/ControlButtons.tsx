import React from 'react';
import './ControlButtons.css';

interface ControlButtonsProps {
  count: number;
  setCount: React.Dispatch<React.SetStateAction<number>>;
}

const ControlButtons: React.FC<ControlButtonsProps> = ({ count, setCount }) => {
  return (
    <div className="buttons-container">
      <button aria-label="Increment" onClick={() => setCount(count + 1)} className="btn btn-increment">+1</button>
      <button aria-label="Decrement" onClick={() => setCount(count - 1)} className="btn btn-decrement">-1</button>
      <button aria-label="Reset" onClick={() => setCount(0)} className="btn btn-reset">Reset</button>
    </div>
  );
};

export default ControlButtons;
