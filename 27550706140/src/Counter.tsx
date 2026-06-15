import React, { useState } from 'react';
import ControlButtons from './ControlButtons';
import './Counter.css';

const Counter: React.FC = () => {
  const [count, setCount] = useState(0);

  return (
    <div className="counter-container">
      <h1>{count}</h1>
      <ControlButtons count={count} setCount={setCount} />
    </div>
  );
};

export default Counter;
