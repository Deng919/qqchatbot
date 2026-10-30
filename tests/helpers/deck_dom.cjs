// Minimal document movement for the existing dashboard data-flow tests.
// Real geometry and animation are exercised by desktop_detail_motion.cjs.
module.exports=function deckNode(node){
 node.style={};
 node.appendChild=function(child){child.remove();this.children.push(child);child.parentElement=this;return child;};
 node.insertBefore=function(child,reference){if(child===reference)return child;child.remove();const index=this.children.indexOf(reference);this.children.splice(index<0?this.children.length:index,0,child);child.parentElement=this;return child;};
 node.remove=function(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(child=>child!==this);this.parentElement=null;};
 node.contains=function(other){return this===other||this.children.some(child=>child.contains(other));};
 Object.defineProperty(node,'nextSibling',{get(){if(!this.parentElement)return null;const siblings=this.parentElement.children;return siblings[siblings.indexOf(this)+1]||null;}});
 return node;
};
