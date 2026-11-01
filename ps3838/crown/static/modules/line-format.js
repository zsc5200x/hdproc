/**
 * Crown line formatting shared by the market books and the paired Pinnacle
 * lookup. The signed value is used as the lookup key, so the sign must survive
 * formatting: dropping a leading "+" turned Crown's receiving line +1.5 into
 * "-1.5" and the paired card could no longer find the Pinnacle row.
 */
export function crownNormalizeLine(value){
  return String(value??"").trim().replace(/\s+/g,"").replace(/[–—−]/g,"-");
}

/** Crown quarter-goal notation: 0.25 -> 0/0.5, +0.75 -> +0.5/1. */
export function crownQuarterLine(text){
  if(!text||text.includes("/"))return text;
  const number=Number(text);
  if(!Number.isFinite(number)||Math.abs(number)<0.000001)return text;
  const magnitude=Math.abs(number);
  const steps=Math.round(magnitude*4);
  const sign=number<0?"-":String(text).trim().startsWith("+")?"+":"";
  if(Math.abs(magnitude*4-steps)<0.000001&&steps%2===1){
    const low=Number((magnitude-0.25).toFixed(2));
    const high=Number((magnitude+0.25).toFixed(2));
    return `${sign}${low}/${high}`;
  }
  return `${sign}${Number(magnitude.toFixed(2))}`;
}

/** Home/away handicap label; the two sides mirror the signed Crown line. */
export function crownHandicapLine(line,side){
  const text=crownQuarterLine(crownNormalizeLine(line));if(!text)return "盘口未知";
  if(text==="0"||text==="0.0")return "0";
  if(text.startsWith("-"))return side==="home"?text:`+${text.slice(1)}`;
  if(text.startsWith("+"))return side==="home"?text:`-${text.slice(1)}`;
  return side==="home"?`-${text}`:`+${text}`;
}
